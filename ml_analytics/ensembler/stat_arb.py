import logging
import queue
import threading
import time
import traceback
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from common.utils import dtutil
from .data_manager import DataManager
from ml_analytics.ensembler.trackers.history_tracker import HistoricalDataTracker
from ml_analytics.ensembler.utils import ReturnType, SignalParameters, RiskParameters
from .position_manager import PositionManager
from .regime_detector import RegimeManager
from .signal_generator import SignalGenerator
from .visualizer import Visualizer


class StatArb:
    def __init__(self,
                 y_feature: str,
                 x_features: List[str],
                 window_days: float = 365,
                 return_type: Dict[str, ReturnType] = None,
                 signal_params: SignalParameters = None,
                 risk_params: RiskParameters = None,
                 poll_interval_seconds: int = 60,
                 periodicity: str = 'DAILY',
                 id_column: str = None,
                 predictors: Optional[List[str]] = None):

        self.y_feature = y_feature
        self.x_features = x_features
        self.all_features = [y_feature] + x_features
        self.poll_interval = poll_interval_seconds

        # Initialize components
        self.data_manager = DataManager(
            features=self.all_features,
            return_type=return_type or {},
            window_days=window_days,
            periodicity=periodicity,
            id_column=id_column
        )

        self.signal_generator = SignalGenerator(
            y_feature=y_feature,
            x_features=x_features,
            signal_params=signal_params,
            predictors=predictors
        )

        self.regime_manager = RegimeManager(
            window_size=self.data_manager.window_units
        )

        self.position_manager = PositionManager(
            risk_params=risk_params
        )

        self.historical_tracker = HistoricalDataTracker()

        self.visualizer = Visualizer(
            y_feature=y_feature,
            x_features=x_features,
            models=predictors,
            historical_tracker=self.historical_tracker
        )

        self.running = False
        self.message_queue = queue.Queue()

    def track_predictions(self, returns_df=None, stats=None):
        try:
            if (returns_df is None) and (stats is None):
                return False

            if not (stats is None) and (isinstance(stats, (dict,))):
                recent_df = self.historical_tracker.get_recent_data(lookback_periods=1, feature=self.y_feature)
                if len(recent_df):
                    asof_date = dtutil.dateadd(recent_df.iloc[-1]['timestamp'], f'1{self.data_manager.periodicity}')
                else:
                    asof_date = dtutil.today()

                for k, v in stats['model_predictions'][self.y_feature].items():
                    self.historical_tracker.add_record(
                        feature=self.y_feature,
                        timestamp=asof_date,
                        predicted_type=k,
                        predicted_return=v,
                        return_type=self.data_manager.return_type.get(self.y_feature, ReturnType.LEVEL)
                    )

            if not (returns_df is None) and (len(returns_df) > 0):
                iloc_indices = range(1, len(returns_df))

                for idx in iloc_indices:
                    row = returns_df.iloc[[idx]].reset_index()
                    asof_date = row['asof_date'].iloc[0]
                    group = row['_group'].iloc[0]

                    for feature in self.all_features:
                        if feature not in returns_df.columns:
                            continue

                        actual_price = returns_df[feature].iloc[idx]

                        self.historical_tracker.add_record(
                            feature=feature,
                            timestamp=asof_date,
                            actual_price=actual_price,
                            return_type=self.data_manager.return_type.get(feature, ReturnType.LEVEL)
                        )

                return True

        except Exception as e:
            logging.error(f"Tracker error: {str(e)}")
            return False

    def process_data(self):
        try:
            # Get data grouped by identifiers
            returns_by_id = self.data_manager.get_data_by_groups()

            if not returns_by_id:
                return {}, {}

            # Process regimes for each group
            regimes_by_group = self.regime_manager.process_regimes(returns_by_id)

            # Generate signals for each group
            signals_by_group = {}
            stats_by_group = {}

            for id_val, returns_df in returns_by_id.items():
                if returns_df.empty:
                    continue

                regime_info = regimes_by_group.get(id_val, {'regime': 'Normal'})
                regime = regime_info.get('regime', 'Normal')

                signals, stats = self.signal_generator.generate_signals(
                    returns_df,
                    regime,
                    id_val
                )
                self.track_predictions(stats=stats)

                # Add regime info to stats
                stats['regime'] = regime
                stats['regime_characteristics'] = regime_info.get('characteristics', {})

                signals_by_group[id_val] = signals
                stats_by_group[id_val] = stats

            # Aggregate signals and stats across groups
            aggregated_signals = self._aggregate_signals(signals_by_group)
            aggregated_stats = self._aggregate_stats(stats_by_group)

            # Add group-specific stats for visualization
            for group_id, group_stats in stats_by_group.items():
                aggregated_stats[group_id] = group_stats

            return aggregated_signals, aggregated_stats

        except Exception as e:
            logging.error(f"Error processing data: {str(e)}")
            logging.error(traceback.format_exc())
            return {}, {}

    def _aggregate_signals(self, signals_by_id):
        aggregated = {}
        for ticker in self.all_features:
            ticker_signals = [signals.get(ticker, 0) for signals in signals_by_id.values()]
            aggregated[ticker] = sum(ticker_signals) / len(ticker_signals) if ticker_signals else 0.0
        return aggregated

    def _aggregate_stats(self, stats_by_id):
        from collections import Counter

        # Find dominant regime
        regimes = [stats.get('regime', 'Normal') for stats in stats_by_id.values()]
        regime_counter = Counter(regimes)
        dominant_regime = regime_counter.most_common(1)[0][0] if regimes else 'Normal'

        # Aggregate model predictions
        model_predictions = {}
        for ticker in self.all_features:
            ticker_preds = {}
            for model in list(self.signal_generator.predictor.selected_predictors) + ['ensemble']:
                preds = []
                for group_stats in stats_by_id.values():
                    model_predictions_dict = group_stats.get('model_predictions', {})
                    ticker_predictions = model_predictions_dict.get(ticker, {})
                    pred = ticker_predictions.get(model)
                    if pred is not None:
                        preds.append(pred)
                ticker_preds[model] = sum(preds) / len(preds) if preds else 0.0
            model_predictions[ticker] = ticker_preds

        # Aggregate volatilities and prices
        volatilities = {}
        current_prices = {}

        for ticker in self.all_features:
            vol_values = []
            price_values = []

            for group_stats in stats_by_id.values():
                vol = group_stats.get('volatilities', {}).get(ticker)
                price = group_stats.get('current_prices', {}).get(ticker)

                if vol is not None:
                    vol_values.append(vol)
                if price is not None:
                    price_values.append(price)

            volatilities[ticker] = sum(vol_values) / len(vol_values) if vol_values else 0.0
            current_prices[ticker] = sum(price_values) / len(price_values) if price_values else 0.0

        # Calculate average regime characteristics
        characteristics = {}
        for key in ['volatility', 'trend', 'correlation']:
            values = [
                stats.get('regime_characteristics', {}).get(key, 0)
                for stats in stats_by_id.values()
            ]
            characteristics[key] = sum(values) / len(values) if values else 0.0

        return {
            'regime': dominant_regime,
            'regime_characteristics': characteristics,
            'model_predictions': model_predictions,
            'volatilities': volatilities,
            'current_prices': current_prices
        }

    def run(self, run_loop=False):
        self.running = True

        def _run_loop():
            try:
                logging.info("Starting initial data collection...")
                self.data_manager.update_data()
                self.track_predictions(returns_df=self.data_manager.data_buffer)

                # Train models on initial data
                returns_by_id = self.data_manager.get_data_by_groups()

                if returns_by_id:
                    # Train regime detectors
                    self.regime_manager.train_detectors(returns_by_id)

                    # Train signal generator models
                    for group_id, returns_df in returns_by_id.items():
                        if not returns_df.empty:
                            self.signal_generator.train_models(returns_df)
                            break  # Just train on the first group for now

                logging.info("Initial setup completed")

                while self.running:
                    try:
                        # Process data and generate signals
                        signals, stats = self.process_data()

                        # Update positions
                        current_prices = stats.get('current_prices', {})
                        volatilities = stats.get('volatilities', {})
                        regime = stats.get('regime', 'Normal')

                        positions = self.position_manager.update_positions(
                            signals,
                            current_prices,
                            volatilities,
                            regime
                        )

                        # Display results
                        output = self.visualizer.format_output(positions, stats)
                        if output:
                            logging.info("\033[2J\033[H")  # Clear console
                            logging.info(output)
                            logging.info(f"\nLast Update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

                        # Sleep or exit
                        time.sleep(self.poll_interval)
                        if not run_loop:
                            break

                        # Update data for next iteration
                        self.data_manager.update_data()

                    except Exception as e:
                        logging.error(f"Error in run loop: {str(e)}")
                        logging.error(traceback.format_exc())
                        time.sleep(self.poll_interval)

            except Exception as e:
                logging.error(f"Error in run thread: {str(e)}")
                logging.error(traceback.format_exc())

        self.processing_thread = threading.Thread(target=_run_loop)
        self.processing_thread.start()

    def stop(self):
        self.running = False
        if hasattr(self, 'processing_thread'):
            self.processing_thread.join()

    def backtest(self, start_date, end_date):
        try:
            # Fetch historical data
            historical_data = self.data_manager.data_fetcher.fetch_data(
                self.all_features,
                start_date,
                end_date,
                elms=[('periodicitySelection', self.data_manager.periodicity)]
            )

            # Process into dataframe
            df = pd.DataFrame(historical_data)
            df['_group'] = 'ALL'  # Add default group

            # Initial training period
            train_size = min(len(df) // 4, 1000)

            # Initialize tracking
            results = []
            positions = {ticker: 0 for ticker in self.all_features}

            # Process each period
            for t in range(train_size, len(df)):
                try:
                    if t % 100 == 0:
                        progress = (t - train_size) / (len(df) - train_size) * 100
                        logging.info(f"Backtest progress: {progress:.1f}%")

                    # Get data window
                    window_data = df.iloc[:t]
                    returns_df = self.data_manager.calculate_returns_dataframe(window_data)

                    # Detect regime
                    regime, regime_chars = self.regime_manager.detect_regime(returns_df)

                    # Generate signals
                    signals, stats = self.signal_generator.generate_signals(
                        returns_df,
                        regime,
                        'backtest'
                    )
                    self.track_predictions(returns_df=returns_df)

                    # Add regime info to stats
                    stats['regime'] = regime
                    stats['regime_characteristics'] = regime_chars

                    # Get current prices
                    current_prices = {}
                    for ticker in self.all_features:
                        if ticker in window_data.columns:
                            current_prices[ticker] = window_data[ticker].iloc[-1]

                    # Get volatilities
                    volatilities = returns_df.std().to_dict()

                    # Update positions
                    new_positions = self.position_manager.update_positions(
                        signals,
                        current_prices,
                        volatilities,
                        regime
                    )

                    # Calculate PnL if we have previous positions
                    if t > train_size:
                        prev_window = df.iloc[:t - 1]
                        prev_prices = {}
                        for ticker in self.all_features:
                            if ticker in prev_window.columns:
                                prev_prices[ticker] = prev_window[ticker].iloc[-1]

                        pnl = self.position_manager.calculate_pnl(current_prices, prev_prices)
                    else:
                        pnl = 0.0

                    # Record results
                    current_date = df.index[t] if hasattr(df, 'index') else t
                    result = {
                        'timestamp': current_date,
                        'pnl': pnl,
                        'regime': regime,
                    }

                    # Record positions, prices, and signals
                    for ticker in self.all_features:
                        result[f'{ticker}_position'] = new_positions.get(ticker, 0)
                        result[f'{ticker}_price'] = current_prices.get(ticker, 0)
                        result[f'{ticker}_signal'] = signals.get(ticker, 0)

                    # Record model predictions
                    if 'model_predictions' in stats:
                        for ticker, models in stats['model_predictions'].items():
                            for model, pred in models.items():
                                result[f'{ticker}_{model}_pred'] = pred

                    results.append(result)
                    positions = new_positions

                except Exception as e:
                    logging.error(f"Error in backtest iteration {t}: {str(e)}")
                    continue

            # Create DataFrame from results
            results_df = pd.DataFrame(results)
            if len(results_df) == 0:
                logging.error("No backtest results generated")
                return pd.DataFrame()

            # Set timestamp as index if it exists
            if 'timestamp' in results_df.columns:
                results_df.set_index('timestamp', inplace=True)

            # Calculate performance metrics
            results_df['cumulative_pnl'] = results_df['pnl'].cumsum()
            results_df['drawdown'] = results_df['cumulative_pnl'] - results_df['cumulative_pnl'].cummax()

            # Calculate returns relative to notional capital
            initial_capital = 100000  # Assumption
            results_df['capital'] = initial_capital + results_df['cumulative_pnl']
            results_df['returns'] = results_df['pnl'] / results_df['capital'].shift(1).fillna(initial_capital)

            # Calculate rolling metrics
            window_size = 20
            results_df['rolling_vol'] = results_df['returns'].rolling(window=window_size).std() * (252 ** 0.5)
            results_df['rolling_sharpe'] = (
                    results_df['returns'].rolling(window=window_size).mean() * 252 /
                    results_df['returns'].rolling(window=window_size).std().replace(0, float('nan')) * (252 ** 0.5)
            ).fillna(0)

            # Calculate trade statistics
            results_df['trade'] = results_df['pnl'] != 0
            total_trades = results_df['trade'].sum()
            winning_trades = (results_df['pnl'] > 0).sum()
            total_return = results_df['cumulative_pnl'].iloc[-1] if len(results_df) > 0 else 0
            max_drawdown = results_df['drawdown'].min() if len(results_df) > 0 else 0
            returns_std = results_df['returns'].std()
            sharpe_ratio = (252 ** 0.5) * results_df['returns'].mean() / returns_std if returns_std > 0 else 0
            win_rate = winning_trades / total_trades if total_trades > 0 else 0

            # Log summary statistics
            logging.info(f"""
            Backtest Results Summary:
            Total Return: {total_return:.2f}
            Max Drawdown: {max_drawdown:.2f}
            Sharpe Ratio: {sharpe_ratio:.2f}
            Win Rate: {win_rate:.2%}
            Total Trades: {total_trades}
            """)

            # Group performance by regime
            regime_performance = results_df.groupby('regime')['pnl'].agg(['sum', 'mean', 'std', 'count'])
            logging.info(f"Performance by Regime:\n{regime_performance}")

            return results_df

        except Exception as e:
            logging.error(f"Error in backtest: {str(e)}")
            logging.error(traceback.format_exc())
            return pd.DataFrame()


def _clear_charts_folder():
    import os
    import shutil

    charts_folder = 'charts'
    if os.path.exists(charts_folder):
        shutil.rmtree(charts_folder)
    os.makedirs(charts_folder, exist_ok=True)

    logging.info(f"Cleared and recreated charts folder: {charts_folder}")


def run_live_example():
    _clear_charts_folder()
    try:
        y_feature = "PCE CMOM Index"
        x_features = [
            # "CPI CHNG Index", "PPI CHNG Index", "AHE MOM% Index", "RSTAMOM Index",
            # "NAPMPMI Index", "NAPMNMI Index", "USURTOT Index", "INJCJC Index",
            'FDIDPGCC Index', 'FDIUPGCC Index', 'FDIUPGBC Index', 'FDIDPGBC Index', 'PPMMCMF Index', 'FDIDPGBB Index',
            'FDIUPGBB Index', 'PPIJLBAB Index',
            "CPRTMVUS Index", "CPSTAIRY Index", "CPRPENER Index", "CPRFHOME Index", "CPRPAXFE Index",
            "CPUMTOT Index", "CPURNSA Index", "CPRHOERY Index", "CPI INDX Index", "CPSHSHLT Index",
            "CPSTUCTR Index"
        ]
        return_type = {
            "NAPMPMI Index": ReturnType.PRICE_DIFF,
            "NAPMNMI Index": ReturnType.PRICE_DIFF,
            "USURTOT Index": ReturnType.PRICE_DIFF,
            "INJCJC Index": ReturnType.PCT_CHANGE,
            "CPRTMVUS Index": ReturnType.PCT_CHANGE, "CPSTAIRY Index": ReturnType.PCT_CHANGE,
            "CPRPENER Index": ReturnType.PCT_CHANGE, "CPRFHOME Index": ReturnType.PCT_CHANGE,
            "CPRPAXFE Index": ReturnType.PCT_CHANGE, "CPUMTOT Index": ReturnType.PCT_CHANGE,
            "CPURNSA Index": ReturnType.PCT_CHANGE, "CPRHOERY Index": ReturnType.PCT_CHANGE,
            "CPI INDX Index": ReturnType.PCT_CHANGE, "CPSHSHLT Index": ReturnType.PCT_CHANGE,
            "CPSTUCTR Index": ReturnType.PCT_CHANGE
        }

        # y_feature = 'EUR Curncy'
        # x_features = ['USGG10YR Index', 'USGG2YR Index', 'GECU2YR Index', 'GECU10YR Index']
        # return_type = {
        #     'USGG10YR Index': ReturnType.PRICE_DIFF,
        #     'USGG2YR Index': ReturnType.PRICE_DIFF,
        #     'GECU2YR Index': ReturnType.PRICE_DIFF,
        #     'GECU10YR Index': ReturnType.PRICE_DIFF,
        #     'EUR Curncy': ReturnType.PCT_CHANGE
        # }

        model = StatArb(
            y_feature=y_feature,
            x_features=x_features,
            return_type=return_type,
            id_column=None,
            window_days=365 * 20,
            signal_params=SignalParameters(
                zscore_entry=2.0,
                zscore_exit=0.0,
                min_correlation=0.3,
                vol_lookback=20,
                halflife=10,
                ml_window=365 * 12,
                regime_window=12
            ),
            risk_params=RiskParameters(
                max_position_size=1.0,
                max_leverage=2.0,
                stop_loss_std=3.0,
                position_timeout=60,
                max_drawdown=0.1
            ),
            periodicity='MONTHLY',
            poll_interval_seconds=900,
            predictors=['elasticnet', 'xgb']
            # predictors=['elasticnet']
            # predictors=['xgb', 'elasticnet', 'kalman', 'ensemble', 'neural', 'autonlinear', 'autolstm', 'automlp',
            #             'autonbeatsx', 'autonhits'],
        )

        logging.info("Starting Statistical Arbitrage Model...")
        model.run()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logging.warning("\nStopping model gracefully...")
            model.stop()
            logging.info("Model stopped successfully")

    except Exception as e:
        logging.error(f"Error initializing or running model: {str(e)}")
        logging.error(traceback.format_exc())


if __name__ == "__main__":
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    config.init('prod')
    setuplogger()
    run_live_example()
