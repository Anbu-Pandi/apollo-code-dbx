#!/usr/bin/env python3
"""
Distributed Statistical Arbitrage CLI
Hybrid approach: Spark ML + neuralforecast DistributedConfig
"""

import argparse
import logging
import sys
import os
from datetime import datetime, timedelta
from typing import Optional



from common.utils.envsetup import get_base_parser, init_app_using_parser
from ml_analytics.ensembler.distributed.config import DistributedStatArbConfig
from ml_analytics.ensembler.distributed.stat_arb import DistributedStatArb


def get_config(config_name: str) -> DistributedStatArbConfig:
    """Get configuration by name"""
    try:
        return DistributedStatArbConfig.create_preset(config_name)
    except ValueError as e:
        logging.error(f"Configuration error: {e}")
        raise


def setup_stat_arb(args) -> DistributedStatArb:
    """Create and configure DistributedStatArb instance"""
    config = get_config(args.config)
    
    # Apply command line overrides
    if hasattr(args, 'neural_nodes') and args.neural_nodes:
        config.neural_config.num_nodes = args.neural_nodes
        
    if hasattr(args, 'neural_devices') and args.neural_devices:
        config.neural_config.devices_per_node = args.neural_devices
        
    if hasattr(args, 'partitions_path') and args.partitions_path:
        config.neural_config.partitions_path = args.partitions_path
        
    if hasattr(args, 'data_table') and args.data_table:
        config.data_source_table = args.data_table
    
    return DistributedStatArb(config)


def get_training_period(target_date: str, years: int = 1) -> tuple[str, str]:
    """Calculate training period ending before target date"""
    target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    train_end = target_dt - timedelta(days=1)
    train_start = train_end - timedelta(days=365 * years)
    return train_start.strftime('%Y-%m-%d'), train_end.strftime('%Y-%m-%d')


def train_command(args):
    """Train hybrid ensemble models"""
    logging.info(f"Training hybrid ensemble: {args.config}")
    
    stat_arb = setup_stat_arb(args)
    metrics = stat_arb.train(args.start_date, args.end_date)
    
    logging.info("Training completed successfully")
    for key, value in metrics.items():
        logging.info(f"  {key}: {value:.4f}")
    
    return metrics


def live_command(args):
    """Generate live signals for a date"""
    logging.info(f"Generating live signals for {args.date}")
    
    stat_arb = setup_stat_arb(args)
    
    # Train model first
    train_start, train_end = get_training_period(args.date, years=20)
    logging.info(f"Training period: {train_start} to {train_end}")
    
    stat_arb.train(train_start, train_end)
    signals_df = stat_arb.generate_live_signals(args.date)
    
    signal_count = signals_df.count()
    logging.info(f"Generated signals for {signal_count} instruments")
    
    return signals_df


def backtest_command(args):
    """Run backtest using hybrid ensemble"""
    logging.info(f"Running backtest: {args.start_date} to {args.end_date}")
    
    stat_arb = setup_stat_arb(args)
    
    # Train model for backtest
    train_start, train_end = get_training_period(args.start_date, years=1)
    logging.info(f"Training period: {train_start} to {train_end}")
    
    stat_arb.train(train_start, train_end)
    backtest_df, metrics = stat_arb.backtest(args.start_date, args.end_date, args.initial_capital)
    
    logging.info("Backtest completed successfully")
    for key, value in metrics.items():
        logging.info(f"  {key}: {value:.4f}")
    
    return backtest_df, metrics


def batch_command(args):
    """Run batch inference"""
    logging.info(f"Running batch inference: {args.start_date} to {args.end_date}")
    
    stat_arb = setup_stat_arb(args)
    
    # Train model for batch inference
    train_start, train_end = get_training_period(args.start_date, years=1)
    logging.info(f"Training period: {train_start} to {train_end}")
    
    stat_arb.train(train_start, train_end)
    
    output_table = args.output_table or f"stat_arb_batch_{args.start_date}_{args.end_date}"
    results_df = stat_arb.batch_inference(args.start_date, args.end_date, output_table)
    
    record_count = results_df.count()
    logging.info(f"Batch inference completed. Processed {record_count} records")
    logging.info(f"Results saved to table: {output_table}")
    
    return results_df


def evaluate_command(args):
    """Evaluate trained model"""
    logging.info(f"Evaluating model: {args.config}")
    
    stat_arb = setup_stat_arb(args)
    
    # Use recent data for evaluation
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=365*10)).strftime('%Y-%m-%d')
    
    logging.info(f"Training for evaluation: {start_date} to {end_date}")
    stat_arb.train(start_date, end_date)
    
    metrics = stat_arb.evaluate_model()
    
    logging.info("Model evaluation completed")
    for key, value in metrics.items():
        logging.info(f"  {key}: {value:.4f}")
    
    return metrics


def create_parser():
    """Create argument parser with subcommands"""
    parser = get_base_parser("distributed_stat_arb")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Common arguments
    def add_common_args(subparser):
        subparser.add_argument("--config", required=True, 
                             choices=["macro", "equity", "fx", "eur_rates"], 
                             help="Configuration preset")
        subparser.add_argument("--data-table", help="Override data source table")
    
    def add_neural_args(subparser):
        subparser.add_argument("--neural-nodes", type=int, help="Number of nodes for neural training")
        subparser.add_argument("--neural-devices", type=int, help="Devices per node")
        subparser.add_argument("--partitions-path", help="Neural model partitions path")
    
    # Train command
    train_parser = subparsers.add_parser("train", help="Train hybrid ensemble models")
    add_common_args(train_parser)
    add_neural_args(train_parser)
    train_parser.add_argument("--start-date", required=True, help="Training start date (YYYY-MM-DD)")
    train_parser.add_argument("--end-date", required=True, help="Training end date (YYYY-MM-DD)")
    
    # Live command
    live_parser = subparsers.add_parser("live", help="Generate live signals")
    add_common_args(live_parser)
    live_parser.add_argument("--date", required=True, help="Signal date (YYYY-MM-DD)")
    
    # Backtest command
    backtest_parser = subparsers.add_parser("backtest", help="Run backtest")
    add_common_args(backtest_parser)
    backtest_parser.add_argument("--start-date", required=True, help="Backtest start date (YYYY-MM-DD)")
    backtest_parser.add_argument("--end-date", required=True, help="Backtest end date (YYYY-MM-DD)")
    backtest_parser.add_argument("--initial-capital", type=float, default=100000, help="Initial capital")
    
    # Batch command
    batch_parser = subparsers.add_parser("batch", help="Run batch inference")
    batch_parser.add_argument("--start-date", required=True, help="Inference start date (YYYY-MM-DD)")
    batch_parser.add_argument("--end-date", required=True, help="Inference end date (YYYY-MM-DD)")
    batch_parser.add_argument("--output-table", help="Output table name")
    batch_parser.add_argument("--data-table", help="Override data source table")
    
    # Evaluate command
    evaluate_parser = subparsers.add_parser("evaluate", help="Evaluate trained model")
    add_common_args(evaluate_parser)
    
    return parser


def main():
    """Main entry point"""
    parser = create_parser()
    init_app_using_parser(parser)

    from data_access.spark.api import sparkmanager
    sparkmanager.init()
    sparkmanager.refresh_spark()
    
    args, _ = parser.parse_known_args()
    
    commands = {
        "train": train_command,
        "live": live_command,
        "backtest": backtest_command,
        "batch": batch_command,
        "evaluate": evaluate_command
    }
    
    if args.command not in commands:
        parser.print_help()
        logging.info("Available commands: train, live, backtest, batch, evaluate")
        return None
    
    try:
        return commands[args.command](args)
    except Exception as e:
        logging.error(f"Command failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main() 