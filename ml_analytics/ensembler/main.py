import logging
import argparse
import time
from datetime import datetime, timedelta

from .stat_arb import StatArb
from .config import StatArbConfig


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("stat_arb.log")
        ]
    )


def run(config_name="default", mode="live", backtest_days=180):
    setup_logging()
    logging.info(f"Starting StatArb in {mode} mode with {config_name} configuration")

    # Get configuration
    if config_name == "default":
        config = StatArbConfig.create_default()
    elif config_name == "equity":
        config = StatArbConfig.create_equity_pair()
    elif config_name == "fx":
        config = StatArbConfig.create_fx_pair()
    else:
        logging.error(f"Unknown configuration: {config_name}")
        return

    # Create model
    model = StatArb(
        y_feature=config.y_feature,
        x_features=config.x_features,
        return_type=config.return_types,
        window_days=config.window_days,
        signal_params=config.signal_params,
        risk_params=config.risk_params,
        poll_interval_seconds=config.poll_interval_seconds,
        periodicity=config.periodicity,
        id_column=config.id_column,
        predictors=config.predictors
    )

    # Run in selected mode
    try:
        if mode == "live":
            logging.info("Starting live trading simulation")
            model.run(run_loop=True)

            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                logging.info("Stopping model gracefully...")
                model.stop()

        elif mode == "backtest":
            logging.info(f"Starting backtest for the last {backtest_days} days")
            end_date = datetime.now()
            start_date = end_date - timedelta(days=backtest_days)

            results = model.backtest(start_date, end_date)

            # Save results
            if not results.empty:
                results.to_csv(f"backtest_results_{config_name}_{start_date.strftime('%Y%m%d')}.csv")
                logging.info(f"Backtest results saved to CSV")

                # Plot results if matplotlib is available
                try:
                    import matplotlib.pyplot as plt

                    plt.figure(figsize=(12, 8))

                    plt.subplot(2, 2, 1)
                    results['cumulative_pnl'].plot()
                    plt.title('Cumulative P&L')
                    plt.grid(True)

                    plt.subplot(2, 2, 2)
                    results['drawdown'].plot(color='red')
                    plt.title('Drawdown')
                    plt.grid(True)

                    plt.subplot(2, 2, 3)
                    results['rolling_sharpe'].plot(color='green')
                    plt.title('Rolling Sharpe Ratio')
                    plt.grid(True)

                    plt.subplot(2, 2, 4)
                    regime_counts = results['regime'].value_counts()
                    regime_counts.plot(kind='bar')
                    plt.title('Regime Distribution')

                    plt.tight_layout()
                    plt.savefig(f"backtest_results_{config_name}_{start_date.strftime('%Y%m%d')}.png")
                    logging.info(f"Backtest chart saved to PNG")
                except:
                    logging.warning("Matplotlib not available, skipping chart generation")

        else:
            logging.error(f"Unknown mode: {mode}")

    except Exception as e:
        logging.error(f"Error in {mode} mode: {str(e)}", exc_info=True)

    logging.info("StatArb execution completed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Statistical Arbitrage Model')
    parser.add_argument('--config', choices=['default', 'equity', 'fx'], default='default',
                        help='Configuration to use')
    parser.add_argument('--mode', choices=['live', 'backtest'], default='live',
                        help='Execution mode')
    parser.add_argument('--backtest_days', type=int, default=180,
                        help='Number of days to backtest')

    args = parser.parse_args()
    run(args.config, args.mode, args.backtest_days)