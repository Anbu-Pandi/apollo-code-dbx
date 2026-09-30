import logging
import uuid
from datetime import datetime
from typing import Optional
import pandas as pd
from threading import Lock
from common.utils.sqlutil import readdfsql, insertdf_batch, execsql
from common.utils.jobutil import JobExecutor
from data_access.dbtables import GENAI_APP

_last_log_ids = {}
_lock = Lock()
_db_logger = None


def _get_db_logger():
    global _db_logger
    if _db_logger is None:
        _db_logger = JobExecutor(exec_type='thread', max_workers=2)
        logging.info("🚀 DB Logger thread initialized")
    return _db_logger


def _write_conversation_to_db(log_data: pd.DataFrame, log_id: str, session_id: str, source_system: str):
    try:
        insertdf_batch('genai_conversation_logs', log_data, app=GENAI_APP)
        logging.info(f"📝 Logged question for session {session_id[:8]} from {source_system} (log_id: {log_id[:8]})")
    except Exception as e:
        logging.error(f"❌ Failed to log conversation: {e}")


def _sanitize_text(text: str) -> str:
    """Sanitize text for SQL insertion by escaping special characters"""
    if not text:
        return ''
    
    # Replace single quotes with double quotes for SQL escaping
    text = text.replace("'", "''")
    
    # Replace backslashes (can cause issues in SQL)
    text = text.replace("\\", "\\\\")
    
    # Ensure text is valid UTF-8 and remove any problematic characters
    try:
        text = text.encode('utf-8', errors='replace').decode('utf-8')
    except Exception:
        pass
    
    return text


def log_conversation(
    session_id: str,
    source_system: str,
    question: str,
    sql_query: Optional[str],
    text_response: str
) -> str:
    log_id = str(uuid.uuid4())
    timestamp = datetime.now()
    asof_date = timestamp.date()
    
    # Sanitize text fields to prevent SQL injection and syntax errors
    question_safe = _sanitize_text(question)
    sql_query_safe = _sanitize_text(sql_query or '')
    text_response_safe = _sanitize_text(text_response)
    
    log_data = pd.DataFrame([{
        'log_id': log_id,
        'timestamp': timestamp,
        'session_id': session_id,
        'source_system': source_system,
        'question': question_safe,
        'sql_query': sql_query_safe,
        'text_response': text_response_safe,
        'user_feedback': '',
        'feedback_sentiment': '',
        'feedback_timestamp': None,
        'asof_date': asof_date
    }])
    
    with _lock:
        _last_log_ids[session_id] = log_id
    
    db_logger = _get_db_logger()
    db_logger.submitjob_async(
        _write_conversation_to_db,
        params=[{'log_data': log_data, 'log_id': log_id, 'session_id': session_id, 'source_system': source_system}],
        use_async_dict=True
    )
    
    return log_id


def _write_run_audit_to_db(audit: pd.DataFrame, run_id: str):
    try:
        insertdf_batch('genai_run_audit', audit, app=GENAI_APP)
        logging.info(f"🧭 Run audit persisted (run_id: {run_id[:12]})")
    except Exception as e:
        logging.error(f"❌ Failed to persist run audit {run_id[:12]}: {e}")


def log_run_audit(
    run_id: str,
    session_id: str,
    source_system: str,
    route: str,
    channel_id: str,
    user_name: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    estimated_cost: float,
    latency_ms: float,
) -> None:
    timestamp = datetime.now()
    audit = pd.DataFrame([{
        'run_id': run_id,
        'timestamp': timestamp,
        'session_id': session_id,
        'source_system': source_system,
        'route': route,
        'channel_id': channel_id,
        'user_name': user_name,
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'cache_read_tokens': cache_read_tokens,
        'cache_write_tokens': cache_write_tokens,
        'estimated_cost': estimated_cost,
        'latency_ms': latency_ms,
        'asof_date': timestamp.date(),
    }])
    db_logger = _get_db_logger()
    db_logger.submitjob_async(
        _write_run_audit_to_db,
        params=[{'audit': audit, 'run_id': run_id}],
        use_async_dict=True,
    )


def _write_feedback_to_db(log_id: str, feedback: str, sentiment: str, feedback_timestamp: datetime):
    try:
        # Use sanitization function for consistency
        feedback_escaped = _sanitize_text(feedback)
        sentiment_escaped = _sanitize_text(sentiment)
        
        update_sql = f"""
        UPDATE genai_conversation_logs
        SET user_feedback = '{feedback_escaped}',
            feedback_sentiment = '{sentiment_escaped}',
            feedback_timestamp = '{feedback_timestamp.isoformat()}'
        WHERE log_id = '{log_id}'
        """
        
        execsql(update_sql, app=GENAI_APP)
        sentiment_emoji = "😊" if sentiment == "positive" else "😞" if sentiment == "negative" else "😐"
        logging.info(f"💬 Updated {sentiment} feedback {sentiment_emoji} for log_id {log_id[:8]}: {feedback[:50]}")
        try:
            from genai.argus_genies.core._learning_promote import on_feedback
            on_feedback(sentiment=sentiment)
        except Exception as e:
            logging.debug(f"learning promoter hook skipped: {e}")
    except Exception as e:
        logging.error(f"❌ Failed to log feedback: {e}")


def log_feedback(session_id: str, feedback: str, sentiment: str = '') -> bool:
    with _lock:
        if session_id not in _last_log_ids:
            logging.warning(f"⚠️ No previous question for session {session_id[:8]}")
            return False
        
        log_id = _last_log_ids[session_id]
    
    feedback_timestamp = datetime.now()
    
    db_logger = _get_db_logger()
    db_logger.submitjob_async(
        _write_feedback_to_db,
        params=[{'log_id': log_id, 'feedback': feedback, 'sentiment': sentiment, 'feedback_timestamp': feedback_timestamp}],
        use_async_dict=True
    )
    
    return True


def clear_session(session_id: str):
    with _lock:
        if session_id in _last_log_ids:
            del _last_log_ids[session_id]
        logging.info(f"🧹 Cleared session {session_id[:8]}")


def soft_reset_session(session_id: str) -> int:
    """Soft reset session - insert reset marker in DB, future loads only get conversations AFTER this marker"""
    try:
        # Count existing conversations
        count_query = f"""
        SELECT COUNT(*) as cnt
        FROM genai_conversation_logs
        WHERE session_id = '{session_id}'
        AND question NOT LIKE '🔄 RESET_MARKER:%'
        """
        count_df = readdfsql(count_query, app=GENAI_APP)
        conversation_count = int(count_df['cnt'].iloc[0]) if not count_df.empty else 0
        
        # Insert reset marker row into DB - this persists across server restarts!
        reset_time = datetime.now()
        marker_log_id = str(uuid.uuid4())
        marker_data = pd.DataFrame([{
            'log_id': marker_log_id,
            'timestamp': reset_time,
            'session_id': session_id,
            'source_system': 'SYSTEM',
            'question': f'🔄 RESET_MARKER: {reset_time.isoformat()}',
            'sql_query': '',
            'text_response': f'Conversation reset at {reset_time.isoformat()}. Previous {conversation_count} messages archived.',
            'user_feedback': '',
            'feedback_sentiment': '',
            'feedback_timestamp': None,
            'asof_date': reset_time.date()
        }])
        
        insertdf_batch('genai_conversation_logs', marker_data, app=GENAI_APP)
        
        # Clear in-memory cache
        clear_session(session_id)
        logging.info(f"🔄 Soft reset session {session_id[:8]} at {reset_time.isoformat()} ({conversation_count} prior conversations hidden)")
        
        return conversation_count
    except Exception as e:
        logging.error(f"❌ Failed to soft reset session: {e}")
        return 0


def get_session_reset_marker(session_id: str):
    """Get the most recent reset marker timestamp for a session from DB"""
    try:
        query = f"""
        SELECT timestamp
        FROM genai_conversation_logs
        WHERE session_id = '{session_id}'
        AND question LIKE '🔄 RESET_MARKER:%'
        ORDER BY timestamp DESC
        LIMIT 1
        """
        df = readdfsql(query, app=GENAI_APP)
        
        if df.empty:
            return None
        
        return df['timestamp'].iloc[0]
    except Exception as e:
        logging.warning(f"⚠️ Failed to get reset marker: {e}")
        return None


def get_session_history(session_id: str, limit: int = 10) -> pd.DataFrame:
    try:
        # Check if there's a reset marker - only load conversations AFTER reset point
        reset_marker = get_session_reset_marker(session_id)
        
        if reset_marker:
            query = f"""
            SELECT question, text_response, timestamp, sql_query, 
                   user_feedback, feedback_sentiment, feedback_timestamp
            FROM genai_conversation_logs
            WHERE session_id = '{session_id}'
            AND timestamp > '{reset_marker.isoformat()}'
            AND question NOT LIKE '🔄 RESET_MARKER:%'
            ORDER BY timestamp DESC
            LIMIT {limit}
            """
            logging.debug(f"📅 Loading history AFTER reset point: {reset_marker.isoformat()}")
        else:
            query = f"""
            SELECT question, text_response, timestamp, sql_query, 
                   user_feedback, feedback_sentiment, feedback_timestamp
            FROM genai_conversation_logs
            WHERE session_id = '{session_id}'
            AND question NOT LIKE '🔄 RESET_MARKER:%'
            ORDER BY timestamp DESC
            LIMIT {limit}
            """
        
        df = readdfsql(query, app=GENAI_APP)
        
        if not df.empty:
            df = df.sort_values('timestamp')
            feedback_count = df['user_feedback'].notna().sum() if 'user_feedback' in df.columns else 0
            reset_status = f" (after reset at {reset_marker.strftime('%H:%M:%S')})" if reset_marker else ""
            logging.info(f"📚 Loaded {len(df)} conversation(s) ({feedback_count} with feedback) for session {session_id[:8]}{reset_status}")
        
        return df
    except Exception as e:
        logging.warning(f"⚠️ Failed to load session history: {e}")
        return pd.DataFrame()


def get_conversation_logs(session_id: str, limit: int = 10):
    """Get conversation logs as list of objects for easier consumption"""
    df = get_session_history(session_id, limit)
    if df.empty:
        return []
    
    logs = []
    for _, row in df.iterrows():
        logs.append(type('ConversationLog', (), {
            'question': row['question'],
            'text_response': row['text_response'],
            'timestamp': row['timestamp'],
            'sql_query': row.get('sql_query'),
            'user_feedback': row.get('user_feedback'),
            'source_system': row.get('source_system', 'unknown')
        }))
    return logs