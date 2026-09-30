import logging
import os
from datetime import datetime
from io import StringIO
from typing import Dict, Any

import numpy as np


class Visualizer:
    def __init__(self, y_feature, x_features, models, historical_tracker=None):
        self.y_feature = y_feature
        self.x_features = x_features
        self.models = models
        self.all_features = [y_feature] + x_features
        self.historical_tracker = historical_tracker

    def format_output(self, positions, stats):
        try:
            text_output = StringIO()

            # Extract main information
            regime = stats.get('regime', 'Unknown')
            regime_chars = stats.get('regime_characteristics', {})
            current_prices = stats.get('current_prices', {})
            model_predictions = stats.get('model_predictions', {})
            volatilities = stats.get('volatilities', {})

            if self.historical_tracker:
                hist_data = self.historical_tracker.get_recent_data(1, self.y_feature)
                current_level = hist_data['actual_price'].iloc[-1] if not hist_data.empty else np.nan
            else:
                current_level = np.nan

            # Build the text output sections
            self._write_regime_section(text_output, regime, regime_chars)
            self._write_prediction_section(text_output, current_level, current_prices, model_predictions)
            self._write_performance_section(text_output)
            self._write_positions_section(text_output, positions, stats)
            self._write_factors_section(text_output, current_prices, volatilities, stats)

            # Add group-specific section if needed
            self._write_group_section(text_output, stats)

            try:
                import plotly.graph_objects as go
                from plotly.subplots import make_subplots
                import plotly.io as pio

                # Create dashboard visualization
                fig = self._create_dashboard_chart(positions, stats)

                # Save to HTML file
                html_template = '''
                <!DOCTYPE html>
                <html><head><meta http-equiv="refresh" content="3600">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>body{{margin:0;padding:20px;font-family:Arial,sans-serif}}
                .container{{display:flex;flex-direction:column;gap:20px}}
                .top-row{{display:flex;gap:20px}}
                .chart{{flex:7;height:90vh;overflow:hidden}}
                .text{{flex:3;font-family:monospace;white-space:pre;background:#f5f5f5;
                padding:20px;border-radius:5px;overflow-y:auto;height:90vh}}</style>
                </head><body><div class="container"><div class="top-row">
                <div class="chart">{chart}</div><div class="text">{text}</div>
                </div></div></body></html>'''

                output_path = os.path.join(os.getcwd(), 'stats_page.html')
                chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=True, config={'responsive': True})

                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(html_template.format(chart=chart_html, text=text_output.getvalue()))

                logging.info(f"Output saved to: {output_path}")
                return text_output.getvalue()

            except Exception as e:
                logging.error(f"Error creating visualization: {str(e)}")
                return text_output.getvalue()

        except Exception as e:
            logging.error(f"Error creating output: {str(e)}")
            return "Error creating output"

    def _write_regime_section(self, output, regime, regime_chars):
        output.write("MARKET REGIME AND CONDITIONS\n")
        output.write("=" * 50 + "\n")
        output.write(f"Current Regime: {regime}\n")
        output.write(f"Volatility Level: {regime_chars.get('volatility', 0):.4f}\n")
        output.write(f"Trend Strength: {regime_chars.get('trend', 0):.4f}\n")
        output.write(f"Average Correlation: {regime_chars.get('correlation', 0):.4f}\n\n")

    def _write_prediction_section(self, output, current_level, current_prices, model_predictions):
        output.write(f"Y PREDICTION ANALYSIS ({self.y_feature})\n")
        output.write("=" * 50 + "\n")
        output.write(f"Current Level:      {current_level:>10.4f}\n")
        output.write("-" * 50 + "\n")
        output.write(f"{'Model':<15} {'Pred Level':>10} {'Diff (bps)':>12} {'Implied Move':>12}\n")
        output.write("-" * 50 + "\n")

        y_current = current_prices.get(self.y_feature, np.nan)
        y_predictions = model_predictions.get(self.y_feature, {})

        for model in self.models:
            pred = y_predictions.get(model, np.nan)
            if not np.isnan(pred) and not np.isnan(y_current):
                pred_level = self._convert_return_to_price(y_current, pred)
                diff_bps = (pred_level - y_current) * 100
                impl_move = "UP" if diff_bps > 1 else "DOWN" if diff_bps < -1 else "NEUTRAL"
            else:
                pred_level = np.nan
                diff_bps = np.nan
                impl_move = "NEUTRAL"

            row = f"{model.upper():<15} {pred_level:>10.4f} {diff_bps:>12.2f} {impl_move:>12}\n"
            output.write(row)

        output.write("\n")

    def _write_performance_section(self, output):
        output.write("PREDICTION PERFORMANCE\n")
        output.write("=" * 50 + "\n")

        if self.historical_tracker:
            metrics = self.historical_tracker.calculate_metrics(self.y_feature)
            output.write(f"Mean Absolute Error (MAE):        {metrics.get('mae', 0):>10.4f}\n")
            output.write(f"Root Mean Squared Error (RMSE):   {metrics.get('rmse', 0):>10.4f}\n")
            output.write(f"Directional Hit Rate:             {metrics.get('hit_rate', 0):>10.2%}\n")
            output.write(f"Number of Observations:           {metrics.get('num_observations', 0):>10d}\n")
        else:
            output.write("No historical tracker available for performance evaluation\n")

        output.write("\n")

    def _write_positions_section(self, output, positions, stats):
        output.write("CURRENT POSITIONS\n")
        output.write("=" * 50 + "\n")
        output.write(f"{'Ticker':<15} {'Position':>10} {'Price':>10} {'Dollar Risk':>12} {'PnL':>10}\n")
        output.write("-" * 50 + "\n")

        total_exposure = 0.0
        total_dollar_risk = 0.0
        total_pnl = 0.0

        for ticker, position in positions.items():
            price = stats.get('current_prices', {}).get(ticker, np.nan)
            volatility = stats.get('volatilities', {}).get(ticker, np.nan)
            dollar_risk = abs(position * price * volatility) if not np.isnan(price) and not np.isnan(
                volatility) else np.nan
            pnl = 0.0  # We would need previous prices to calculate this

            total_exposure += abs(position * price) if not np.isnan(price) else 0.0
            total_dollar_risk += dollar_risk if not np.isnan(dollar_risk) else 0.0
            total_pnl += pnl

            row = f"{ticker:<15} {position:>10.2f} {price:>10.4f} {dollar_risk:>12.4f} {pnl:>10.2f}\n"
            output.write(row)

        output.write("-" * 50 + "\n")
        output.write(f"Total Exposure:      {total_exposure:>10.2f}\n")
        output.write(f"Total Dollar Risk:   {total_dollar_risk:>10.2f}\n")
        output.write(f"Total PnL:           {total_pnl:>10.2f}\n\n")

    def _write_factors_section(self, output, current_prices, volatilities, stats):
        output.write("=" * 100 + "\n")
        output.write("EXPLANATORY FACTORS (X variables)\n")
        output.write("=" * 100 + "\n")

        header = f"{'Factor':<15} {'Level':>10} {'Vol':>10} {'Corr w/Y':>10} {'Beta':>10} {'Kalman Beta':>10} {'Signal':>10}\n"
        output.write(header)
        output.write("-" * 100 + "\n")

        for ticker in self.x_features:
            current = current_prices.get(ticker, np.nan)
            vol = volatilities.get(ticker, np.nan)
            ticker_stats = stats.get(f'{ticker}_stats', {})

            row = (f"{ticker:<15} {current:>10.4f} {vol:>10.6f} "
                   f"{ticker_stats.get('correlation', np.nan):>10.4f} "
                   f"{ticker_stats.get('beta', np.nan):>10.4f} "
                   f"{ticker_stats.get('kalman_beta', np.nan):>10.4f} "
                   f"{ticker_stats.get('signal', np.nan):>10.4f}\n")
            output.write(row)

    def _write_group_section(self, output, stats):
        group_stats = {k: v for k, v in stats.items() if isinstance(k, tuple) and len(k) > 0}

        if not group_stats:
            return

        output.write("\nGROUP-SPECIFIC STATISTICS\n")
        output.write("=" * 50 + "\n")

        for group_key, group_data in list(group_stats.items())[:3]:  # Show max 3 groups
            group_name = str(group_key)

            output.write(f"\n{group_name}\n")
            output.write("-" * 50 + "\n")

            # Regime info
            regime = group_data.get('regime', 'Unknown')
            output.write(f"Regime: {regime}\n")

            # Top correlations
            output.write("\nTop Correlations with Y:\n")
            correlations = []
            for ticker in self.x_features:
                ticker_stats = group_data.get(f'{ticker}_stats', {})
                corr = ticker_stats.get('correlation', np.nan)
                signal = ticker_stats.get('signal', np.nan)
                if not np.isnan(corr):
                    correlations.append((ticker, corr, signal))

            # Sort by absolute correlation and show top 5
            top_corrs = sorted(correlations, key=lambda x: abs(x[1]) if not np.isnan(x[1]) else 0, reverse=True)[:5]

            for ticker, corr, signal in top_corrs:
                output.write(f"{ticker:<10} Corr: {corr:>8.4f} Signal: {signal:>8.4f}\n")

            output.write("\n")

    def _convert_return_to_price(self, current_price, return_value, return_type='LEVEL'):
        if return_type == 'LEVEL':
            return return_value
        elif return_type == 'PCT_CHANGE':
            return current_price * (1 + return_value)
        elif return_type == 'PRICE_DIFF':
            return current_price + return_value
        elif return_type == 'LOG_RETURN':
            return current_price * np.exp(return_value)
        else:
            return np.nan

    def _create_dashboard_chart(self, positions: Dict[str, float], stats: Dict[str, Any]):
        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots

            fig = make_subplots(
                rows=2, cols=2,
                subplot_titles=(
                    f'{self.y_feature} Price & Predictions',
                    'X Variables',
                    'Positions',
                    'Risk Metrics'
                )
            )

            # 1. Y-feature time series and predictions
            if self.historical_tracker:
                hist_data = self.historical_tracker.get_recent_data(-1, self.y_feature)
                if not hist_data.empty:
                    fig.add_trace(
                        go.Scatter(
                            x=hist_data['timestamp'],
                            y=hist_data['actual_price'],
                            name=self.y_feature,
                            line=dict(color='blue')
                        ),
                        row=1, col=1
                    )

                    predictions = self.historical_tracker.get_predictions(self.y_feature)
                    for pt, _df in predictions.groupby('predicted_type'):
                        fig.add_trace(
                            go.Scatter(
                                x=_df['timestamp'],
                                y=_df['predicted_price'],
                                name=pt,
                                line=dict(dash='dot'),
                                mode='markers'
                            ),
                            row=1, col=1
                        )

            # 2. X Variables normalized returns (dummy data for now)
            for i, ticker in enumerate(self.x_features):  # Limit to 4 for clarity
                hist_data = self.historical_tracker.get_recent_data(-1, ticker)
                if not hist_data.empty:
                    fig.add_trace(
                        go.Scatter(
                            x=hist_data['timestamp'],
                            y=hist_data['actual_price'],
                            name=ticker
                        ),
                        row=1, col=2
                    )

            # 3. Positions
            pos_data = [positions.get(ticker, 0) for ticker in self.all_features]
            colors = ['green' if p > 0 else 'red' if p < 0 else 'gray' for p in pos_data]

            fig.add_trace(
                go.Bar(
                    x=self.all_features,
                    y=pos_data,
                    marker_color=colors,
                    name='Positions'
                ),
                row=2, col=1
            )

            # 4. Risk Metrics (Volatilities)
            vols = [stats.get('volatilities', {}).get(ticker, 0) for ticker in self.all_features]
            fig.add_trace(
                go.Bar(
                    x=self.all_features,
                    y=vols,
                    name='Volatility',
                    marker_color='purple'
                ),
                row=2, col=2
            )

            # Update layout with safer margins
            fig.update_layout(
                height=800,
                margin=dict(l=50, r=50, t=80, b=50),
                showlegend=True,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                title_text=f"StatArb Analysis - {self.y_feature} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

            return fig

        except Exception as e:
            logging.error(f"Error creating chart: {str(e)}")
            import plotly.graph_objects as go
            error_fig = go.Figure()
            error_fig.add_annotation(
                text=f"Error creating chart: {str(e)}",
                xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False,
                font=dict(size=14, color="red")
            )
            return error_fig
