import numpy as np
import scipy.stats as stats
from sklearn.linear_model import Ridge, Lasso, ElasticNet


class BetaCalc:
    @staticmethod
    def exp_weights(n, half_life):
        if half_life <= 0:
            return np.ones(n) / n

        decay = np.exp(np.log(0.5) / half_life)
        weights = np.array([decay ** i for i in range(n)])
        weights = weights[::-1]

        return weights / weights.sum()

    @staticmethod
    def vol_weights(returns, lambda_decay=0.94):
        sq_returns = returns ** 2
        vol = np.zeros_like(returns)
        vol[0] = sq_returns[0]
        for t in range(1, len(returns)):
            vol[t] = lambda_decay * vol[t - 1] + (1 - lambda_decay) * sq_returns[t]
        vol = np.sqrt(vol)

        weights = 1 / (vol + 1e-8)
        return weights / np.sum(weights)

    @staticmethod
    def clean_outliers(x, n_std=3.0):
        mean = np.mean(x)
        std = np.std(x)
        return np.clip(x, mean - n_std * std, mean + n_std * std)

    @staticmethod
    def detect_regimes(returns, window=30):
        if len(returns) <= window:
            return np.array([])

        rolling_corr = np.zeros(len(returns) - window)
        for i in range(len(rolling_corr)):
            corr = np.corrcoef(returns[i:i + window], range(window))[0, 1]
            rolling_corr[i] = corr

        return np.where(np.diff(np.sign(rolling_corr)))[0]

    @staticmethod
    def beta(y, X, alpha=True, weights=None, clean=False, n_std=3.0, include_alpha=False):
        offset = 1
        if include_alpha:
            offset = 0
        null_return = np.zeros((X.shape[1] if X.ndim > 1 else 1) + (1 - offset))
        if len(y) <= 1:
            return null_return

        if clean:
            y = BetaCalc.clean_outliers(y, n_std)
            if X.ndim == 1:
                X = BetaCalc.clean_outliers(X, n_std)
            else:
                for i in range(X.shape[1]):
                    X[:, i] = BetaCalc.clean_outliers(X[:, i], n_std)

        if X.ndim == 1:
            X = X.reshape(-1, 1)

        if weights is None:
            weights = np.ones(len(y)) / len(y)

        weights = weights / weights.sum()
        sqrt_weights = np.sqrt(weights)
        y_weighted = y * sqrt_weights

        if alpha:
            X_with_intercept = np.column_stack((np.ones(len(X)), X))
            X_weighted = X_with_intercept * sqrt_weights.reshape(-1, 1)
        else:
            X_weighted = X * sqrt_weights.reshape(-1, 1)

        try:
            XtX_inv = np.linalg.inv(X_weighted.T.dot(X_weighted))
            XtY = X_weighted.T.dot(y_weighted)
            coeffs = XtX_inv.dot(XtY)

            return coeffs[offset:]
        except np.linalg.LinAlgError:
            return null_return

    @staticmethod
    def beta_ci(y, X, beta, alpha=0.05):
        n = len(y)
        k = X.shape[1] if X.ndim > 1 else 1

        if X.ndim == 1:
            X = X.reshape(-1, 1)

        y_pred = X.dot(beta)
        residuals = y - y_pred
        sigma2 = np.sum(residuals ** 2) / (n - k)

        try:
            var_beta = sigma2 * np.diag(np.linalg.inv(X.T.dot(X)))
            se_beta = np.sqrt(var_beta)

            t_value = stats.t.ppf(1 - alpha / 2, n - k)

            lower_bound = beta - t_value * se_beta
            upper_bound = beta + t_value * se_beta

            return lower_bound, upper_bound
        except np.linalg.LinAlgError:
            return beta, beta

    @staticmethod
    def r_squared(y, X, beta, include_alpha=False):
        if X.ndim == 1:
            X = X.reshape(-1, 1)

        if include_alpha:
            X = np.column_stack((np.ones(len(X)), X))

        y_pred = X.dot(beta)
        ss_total = np.sum((y - np.mean(y)) ** 2)
        ss_residual = np.sum((y - y_pred) ** 2)

        r2 = 1 - (ss_residual / (ss_total + 1e-10))

        return max(0, min(1, r2))

    @staticmethod
    def shrink_beta(beta, target=1.0, intensity=0.3):
        if not isinstance(beta, np.ndarray):
            beta = np.array([beta])

        intensity = max(0, min(1, intensity))

        return beta * (1 - intensity) + target * intensity

    @staticmethod
    def ridge_beta(y, X, alpha=1.0, fit_intercept=True, weights=None):
        if X.ndim == 1:
            X = X.reshape(-1, 1)

        ridge = Ridge(alpha=alpha, fit_intercept=fit_intercept)

        if weights is not None:
            ridge.fit(X, y, sample_weight=weights)
        else:
            ridge.fit(X, y)

        return ridge.coef_

    @staticmethod
    def lasso_beta(y, X, alpha=1.0, fit_intercept=True, weights=None, max_iter=1000):
        if X.ndim == 1:
            X = X.reshape(-1, 1)

        lasso = Lasso(alpha=alpha, fit_intercept=fit_intercept, max_iter=max_iter)

        if weights is not None:
            lasso.fit(X, y, sample_weight=weights)
        else:
            lasso.fit(X, y)

        return lasso.coef_

    @staticmethod
    def elastic_net_beta(y, X, alpha=1.0, l1_ratio=0.5, fit_intercept=True, weights=None, max_iter=1000):
        if X.ndim == 1:
            X = X.reshape(-1, 1)

        elastic = ElasticNet(alpha=alpha, l1_ratio=l1_ratio, fit_intercept=fit_intercept, max_iter=max_iter)

        if weights is not None:
            elastic.fit(X, y, sample_weight=weights)
        else:
            elastic.fit(X, y)

        return elastic.coef_

    @staticmethod
    def bayesian_beta(y, X, prior_mean=1.0, prior_precision=0.1):
        if X.ndim == 1:
            X = X.reshape(-1, 1)

        k = X.shape[1]

        if isinstance(prior_mean, (int, float)):
            prior_mean = np.ones(k) * prior_mean

        if isinstance(prior_precision, (int, float)):
            prior_precision = np.eye(k) * prior_precision

        XtX = X.T.dot(X)
        XtY = X.T.dot(y)

        try:
            posterior_precision = prior_precision + XtX
            posterior_mean = np.linalg.inv(posterior_precision).dot(
                prior_precision.dot(prior_mean) + XtY
            )
            return posterior_mean
        except np.linalg.LinAlgError:
            return prior_mean

    @staticmethod
    def up_down_beta(y, X, fit_intercept=True):
        if X.ndim > 1 and X.shape[1] == 1:
            X = X.flatten()
        elif X.ndim > 1:
            raise ValueError("Asymmetric beta only supports single-factor models")

        X_pos = X.copy()
        X_pos[X < 0] = 0

        X_neg = X.copy()
        X_neg[X > 0] = 0

        X_combined = np.column_stack((X_pos, X_neg))

        return BetaCalc.beta(y, X_combined, alpha=fit_intercept)

    @staticmethod
    def rolling_beta(y, X, window=52, step=1, method='ols', fit_intercept=True,
                     use_exp_weights=False, half_life=26, weight_fn=None,
                     clean=False, calc_ci=False, ci_alpha=0.05,
                     reg_alpha=1.0, l1_ratio=0.5):
        if len(y) < window:
            raise ValueError(f"Input length {len(y)} is less than window size {window}")

        if X.ndim == 1:
            X = X.reshape(-1, 1)

        k = X.shape[1]
        n_windows = (len(y) - window) // step + 1

        betas = np.zeros((n_windows, k))
        r2 = np.zeros(n_windows)

        if calc_ci:
            lower = np.zeros((n_windows, k))
            upper = np.zeros((n_windows, k))

        for i in range(n_windows):
            start_idx = i * step
            end_idx = start_idx + window

            y_window = y[start_idx:end_idx]
            X_window = X[start_idx:end_idx, :]

            weights = None
            if weight_fn is not None:
                weights = weight_fn(y_window, X_window)
            elif use_exp_weights:
                weights = BetaCalc.exp_weights(len(y_window), half_life)

            if method == 'ols':
                beta = BetaCalc.beta(
                    y_window, X_window,
                    alpha=fit_intercept,
                    weights=weights,
                    clean=clean
                )
            elif method == 'ridge':
                beta = BetaCalc.ridge_beta(
                    y_window, X_window,
                    alpha=reg_alpha,
                    fit_intercept=fit_intercept,
                    weights=weights
                )
            elif method == 'lasso':
                beta = BetaCalc.lasso_beta(
                    y_window, X_window,
                    alpha=reg_alpha,
                    fit_intercept=fit_intercept,
                    weights=weights
                )
            elif method == 'elastic_net':
                beta = BetaCalc.elastic_net_beta(
                    y_window, X_window,
                    alpha=reg_alpha,
                    l1_ratio=l1_ratio,
                    fit_intercept=fit_intercept,
                    weights=weights
                )
            elif method == 'up_down':
                beta = BetaCalc.up_down_beta(
                    y_window, X_window,
                    fit_intercept=fit_intercept
                )
            elif method == 'bayesian':
                beta = BetaCalc.bayesian_beta(
                    y_window, X_window
                )
            else:
                raise ValueError(f"Unknown method: {method}")

            betas[i, :] = beta
            r2[i] = BetaCalc.r_squared(y_window, X_window, beta, include_alpha=fit_intercept)

            if calc_ci:
                lb, ub = BetaCalc.beta_ci(y_window, X_window, beta, ci_alpha)
                lower[i, :] = lb
                upper[i, :] = ub

        results = {
            'beta': betas,
            'r2': r2
        }

        if calc_ci:
            results['lower'] = lower
            results['upper'] = upper

        return results


class BetaAnalyzer:
    @staticmethod
    def find_optimal_regularization(y, X, method='ridge', alphas=None,
                                    l1_ratios=None, n_folds=5,
                                    fit_intercept=True, clean=False):
        from sklearn.linear_model import RidgeCV, LassoCV, ElasticNetCV
        from sklearn.model_selection import KFold

        if clean:
            y = BetaCalc.clean_outliers(y)
            if X.ndim == 1:
                X = BetaCalc.clean_outliers(X)
            else:
                for i in range(X.shape[1]):
                    X[:, i] = BetaCalc.clean_outliers(X[:, i])

        if X.ndim == 1:
            X = X.reshape(-1, 1)

        if alphas is None:
            alphas = np.logspace(-3, 1, 20)

        kf = KFold(n_splits=n_folds, shuffle=True, random_state=42)

        if method == 'ridge':
            model = RidgeCV(alphas=alphas, fit_intercept=fit_intercept, cv=kf)
            model.fit(X, y)

            return {
                'alpha': model.alpha_,
                'coef': model.coef_,
                'intercept': model.intercept_ if fit_intercept else None,
                'cv_values': model.cv_values_ if hasattr(model, 'cv_values_') else None
            }

        elif method == 'lasso':
            model = LassoCV(alphas=alphas, fit_intercept=fit_intercept, cv=kf, max_iter=10000)
            model.fit(X, y)

            return {
                'alpha': model.alpha_,
                'coef': model.coef_,
                'intercept': model.intercept_ if fit_intercept else None,
                'mse_path': model.mse_path_ if hasattr(model, 'mse_path_') else None
            }

        elif method == 'elastic_net':
            if l1_ratios is None:
                l1_ratios = np.linspace(0.1, 0.9, 9)

            model = ElasticNetCV(
                l1_ratio=l1_ratios,
                alphas=alphas,
                fit_intercept=fit_intercept,
                cv=kf,
                max_iter=10000
            )
            model.fit(X, y)

            return {
                'alpha': model.alpha_,
                'l1_ratio': model.l1_ratio_,
                'coef': model.coef_,
                'intercept': model.intercept_ if fit_intercept else None,
                'mse_path': model.mse_path_ if hasattr(model, 'mse_path_') else None
            }

        else:
            raise ValueError(f"Unknown method: {method}")

    @staticmethod
    def summarize_betas(betas):
        if betas.ndim == 1:
            betas = betas.reshape(-1, 1)

        n_factors = betas.shape[1]

        summary = {
            'mean': np.mean(betas, axis=0),
            'median': np.median(betas, axis=0),
            'std': np.std(betas, axis=0),
            'min': np.min(betas, axis=0),
            'max': np.max(betas, axis=0),
            'vol': np.std(betas, axis=0) / np.abs(np.mean(betas, axis=0) + 1e-10),
            'n': len(betas)
        }

        for i in range(n_factors):
            key = f'factor_{i + 1}' if n_factors > 1 else 'beta'
            summary[f'{key}_q'] = np.percentile(betas[:, i], [25, 50, 75])

        return summary

    @staticmethod
    def stability(rolling_betas, window=12):
        if rolling_betas.ndim == 1:
            rolling_betas = rolling_betas.reshape(-1, 1)

        n_factors = rolling_betas.shape[1]
        stability = {}

        for i in range(n_factors):
            factor_betas = rolling_betas[:, i]

            roll_std = np.array([np.std(factor_betas[max(0, j - window):j + 1])
                                 for j in range(len(factor_betas))])

            if len(factor_betas) > 1:
                autocorr = np.corrcoef(factor_betas[:-1], factor_betas[1:])[0, 1]
            else:
                autocorr = np.nan

            factor_key = f'factor_{i + 1}' if n_factors > 1 else 'beta'
            stability[factor_key] = {
                'roll_std': np.mean(roll_std),
                'autocorr': autocorr,
                'trend': np.polyfit(np.arange(len(factor_betas)), factor_betas, 1)[0]
            }

        return stability


if __name__ == '__main__':
    def _get_returns_data():
        from apo_config.config import config
        from common.utils import sqlutil
        import pandas as pd

        config.init('prod')

        sql = '''
              select *
              from dbo.security_ts_hist
              where (sec_id ilike 'CD%HY%'
        or sec_id ilike '%tsy_10%')
                and frequency = 'WEEKLY'
                and attribute ilike '%ret%'
        and value is not null
              order by asof_date \
              '''

        df = sqlutil.readdfsql(sql, app='databricks')
        df = pd.pivot_table(df, index=['asof_date'], columns=['sec_id'], values='value', aggfunc='mean', dropna=False)
        min_size = df.dropna().shape[0]

        market_returns = df['CDX_HY_OTR'].ffill().pct_change().values

        asset_returns = df['tsy_10y'].fillna(0).values

        return asset_returns[-min_size:], market_returns[-min_size:]


    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    # Parameters
    # np.random.seed(35)  # For reproducibility
    n_samples = 520
    target_beta = 0
    std_beta_chg = 0  # Standard deviation of beta
    target_corr = 0.5
    market_vol = 0.16  # Market volatility
    half_life = 12  # Half-life for exponential weighting

    # Calculate rolling betas with different methods
    window = 52  # Rolling window size

    # Generate market returns
    market_returns = np.random.normal(0, market_vol / (252 ** 0.5), n_samples)

    # Generate a time-varying beta from a normal distribution
    time_varying_betas = np.random.normal(target_beta, std_beta_chg / (252 ** 0.5), n_samples)

    # To maintain both beta and correlation, we need to carefully construct asset returns
    # Calculate the appropriate asset volatility based on the relationship between beta and correlation:
    # beta = corr * (asset_vol / market_vol)
    # Therefore: asset_vol = beta * market_vol / corr

    # Generate asset returns
    asset_returns = np.zeros(n_samples)
    z = np.random.standard_normal(n_samples)  # Independent noise

    for i in range(n_samples):
        beta = time_varying_betas[i]

        asset_vol = beta * market_vol / target_corr / (252 ** 0.5)

        # Calculate how much of the asset volatility is explained by the market (systematic)
        systematic_vol = target_corr * asset_vol

        # The rest is idiosyncratic
        idiosyncratic_vol = asset_vol * np.sqrt(1 - target_corr ** 2)

        # Generate the return components
        systematic_return = beta * market_returns[i] * (systematic_vol / (beta * market_vol / (252 ** 0.5)))
        idiosyncratic_return = idiosyncratic_vol * z[i]

        # Total return
        asset_returns[i] = systematic_return + idiosyncratic_return

    asset_returns, market_returns = _get_returns_data()
    n_samples = len(asset_returns)
    time_varying_betas = [0.0] * n_samples

    # Verify actual correlation and beta
    actual_corr = np.corrcoef(asset_returns, market_returns)[0, 1]
    beta_ols = BetaCalc.beta(asset_returns, market_returns)[0]
    r2_full = BetaCalc.r_squared(asset_returns, market_returns.reshape(-1, 1), np.array([beta_ols]))

    print(f"Target correlation: {target_corr}, Actual correlation: {actual_corr:.4f}")
    print(f"Target beta: {target_beta}, Actual beta: {beta_ols:.4f}")
    print(f"Expected R²: {target_corr ** 2:.4f}, Actual R²: {r2_full:.4f}")

    # 1. Standard unweighted rolling beta
    unweighted_results = BetaCalc.rolling_beta(
        asset_returns, market_returns,
        window=window,
        step=1,
        method='ols',
        calc_ci=True
    )

    # 2. Exponentially weighted rolling beta
    exp_weighted_results = BetaCalc.rolling_beta(
        asset_returns, market_returns,
        window=window,
        step=1,
        method='ols',
        use_exp_weights=True,
        half_life=half_life,
        calc_ci=True
    )

    # Calculate mean R-squared for rolling estimates
    mean_r2_unweighted = np.mean(unweighted_results['r2'])
    mean_r2_exp_weighted = np.mean(exp_weighted_results['r2'])

    # Calculate tracking error (RMSE between estimated and true beta)
    unweighted_tracking_error = np.sqrt(
        np.mean((unweighted_results['beta'].flatten() - time_varying_betas[window - 1:]) ** 2))
    exp_weighted_tracking_error = np.sqrt(
        np.mean((exp_weighted_results['beta'].flatten() - time_varying_betas[window - 1:]) ** 2))

    # Create time axis for plots
    time_axis = np.arange(len(unweighted_results['beta']))
    true_beta_plot = time_varying_betas[window - 1:]  # Align with rolling window results

    # Create an interactive Plotly figure with three subplots
    fig = make_subplots(
        rows=3, cols=1,
        subplot_titles=(
            "Asset Returns vs Market Returns (with Normally Distributed Beta)",
            "Comparison of Rolling Beta Estimation Methods vs True Time-Varying Beta",
            "Distribution of Beta Estimates vs True Beta Distribution"
        ),
        specs=[[{"secondary_y": False}], [{"secondary_y": True}], [{"secondary_y": False}]],
        vertical_spacing=0.12,
        row_heights=[0.25, 0.5, 0.25]
    )

    # Plot 1: Scatter plot of returns with regression line
    fig.add_trace(
        go.Scatter(
            x=market_returns,
            y=asset_returns,
            mode='markers',
            name='Daily Returns',
            marker=dict(color='blue', opacity=0.7, size=6),
            hovertemplate='Market: %{x:.4f}<br>Asset: %{y:.4f}'
        ),
        row=1, col=1
    )

    # Add OLS regression line
    x_sorted = np.sort(market_returns)
    fig.add_trace(
        go.Scatter(
            x=x_sorted,
            y=beta_ols * x_sorted,
            mode='lines',
            name=f'OLS Beta: {beta_ols:.4f}',
            line=dict(color='green', width=2)
        ),
        row=1, col=1
    )

    # Add mean beta line
    fig.add_trace(
        go.Scatter(
            x=x_sorted,
            y=target_beta * x_sorted,
            mode='lines',
            name=f'Target Beta: {target_beta}',
            line=dict(color='red', width=2, dash='dash')
        ),
        row=1, col=1
    )

    # Plot 2: Time series of betas, confidence intervals, and R-squared
    # Unweighted rolling beta
    fig.add_trace(
        go.Scatter(
            x=time_axis,
            y=unweighted_results['beta'].flatten(),
            mode='lines',
            name='Unweighted Rolling Beta',
            line=dict(color='blue', width=2),
            hovertemplate='Time: %{x}<br>Beta: %{y:.4f}'
        ),
        row=2, col=1
    )

    # Exponentially weighted rolling beta
    fig.add_trace(
        go.Scatter(
            x=time_axis,
            y=exp_weighted_results['beta'].flatten(),
            mode='lines',
            name='Exp. Weighted Rolling Beta',
            line=dict(color='green', width=2),
            hovertemplate='Time: %{x}<br>Beta: %{y:.4f}'
        ),
        row=2, col=1
    )

    # True time-varying beta
    fig.add_trace(
        go.Scatter(
            x=time_axis,
            y=true_beta_plot,
            mode='lines',
            name='True Time-Varying Beta',
            line=dict(color='red', width=1.5, dash='solid'),
            opacity=0.7,
            hovertemplate='Time: %{x}<br>True Beta: %{y:.4f}'
        ),
        row=2, col=1
    )

    # Mean beta reference
    fig.add_trace(
        go.Scatter(
            x=[time_axis[0], time_axis[-1]],
            y=[target_beta, target_beta],
            mode='lines',
            name=f'Target Beta ({target_beta})',
            line=dict(color='purple', width=2, dash='dash')
        ),
        row=2, col=1
    )

    # Unweighted confidence intervals
    fig.add_trace(
        go.Scatter(
            x=np.concatenate([time_axis, time_axis[::-1]]),
            y=np.concatenate([unweighted_results['upper'].flatten(), unweighted_results['lower'].flatten()[::-1]]),
            fill='toself',
            fillcolor='rgba(0, 0, 255, 0.2)',
            line=dict(color='rgba(0, 0, 255, 0)'),
            name='Unweighted 95% CI',
            showlegend=True,
            hoverinfo='skip'
        ),
        row=2, col=1
    )

    # Exponentially weighted confidence intervals
    fig.add_trace(
        go.Scatter(
            x=np.concatenate([time_axis, time_axis[::-1]]),
            y=np.concatenate([exp_weighted_results['upper'].flatten(), exp_weighted_results['lower'].flatten()[::-1]]),
            fill='toself',
            fillcolor='rgba(0, 255, 0, 0.2)',
            line=dict(color='rgba(0, 255, 0, 0)'),
            name='Exp. Weighted 95% CI',
            showlegend=True,
            hoverinfo='skip'
        ),
        row=2, col=1
    )

    # R-squared on secondary y-axis
    fig.add_trace(
        go.Scatter(
            x=time_axis,
            y=unweighted_results['r2'],
            mode='lines',
            name='Unweighted R²',
            line=dict(color='blue', width=1.5, dash='dot'),
            opacity=0.7,
            hovertemplate='Time: %{x}<br>R²: %{y:.4f}'
        ),
        row=2, col=1, secondary_y=True
    )

    fig.add_trace(
        go.Scatter(
            x=time_axis,
            y=exp_weighted_results['r2'],
            mode='lines',
            name='Exp. Weighted R²',
            line=dict(color='green', width=1.5, dash='dot'),
            opacity=0.7,
            hovertemplate='Time: %{x}<br>R²: %{y:.4f}'
        ),
        row=2, col=1, secondary_y=True
    )

    # Expected R² reference line
    fig.add_trace(
        go.Scatter(
            x=[time_axis[0], time_axis[-1]],
            y=[target_corr ** 2, target_corr ** 2],
            mode='lines',
            name=f'Expected R² (corr²={target_corr ** 2:.4f})',
            line=dict(color='gray', width=1.5, dash='dash'),
            opacity=0.7
        ),
        row=2, col=1, secondary_y=True
    )

    # Plot 3: Distribution histograms
    unweighted_betas = unweighted_results['beta'].flatten()
    exp_weighted_betas = exp_weighted_results['beta'].flatten()

    # Create bins for histograms
    bins = np.linspace(
        min(np.min(time_varying_betas), np.min(unweighted_betas), np.min(exp_weighted_betas)) - 0.1,
        max(np.max(time_varying_betas), np.max(unweighted_betas), np.max(exp_weighted_betas)) + 0.1,
        50
    )

    # True beta distribution
    fig.add_trace(
        go.Histogram(
            x=time_varying_betas,
            histnorm='probability density',
            name='True Beta Distribution',
            marker=dict(color='rgba(255, 0, 0, 0.6)'),
            opacity=0.7,
            xbins=dict(start=bins[0], end=bins[-1], size=(bins[-1] - bins[0]) / 50),
            hovertemplate='Beta: %{x:.4f}<br>Density: %{y:.4f}'
        ),
        row=3, col=1
    )

    # Unweighted beta distribution
    fig.add_trace(
        go.Histogram(
            x=unweighted_betas,
            histnorm='probability density',
            name='Unweighted Beta Distribution',
            marker=dict(color='rgba(0, 0, 255, 0.6)'),
            opacity=0.7,
            xbins=dict(start=bins[0], end=bins[-1], size=(bins[-1] - bins[0]) / 50),
            hovertemplate='Beta: %{x:.4f}<br>Density: %{y:.4f}'
        ),
        row=3, col=1
    )

    # Exponentially weighted beta distribution
    fig.add_trace(
        go.Histogram(
            x=exp_weighted_betas,
            histnorm='probability density',
            name='Exp. Weighted Beta Distribution',
            marker=dict(color='rgba(0, 255, 0, 0.6)'),
            opacity=0.7,
            xbins=dict(start=bins[0], end=bins[-1], size=(bins[-1] - bins[0]) / 50),
            hovertemplate='Beta: %{x:.4f}<br>Density: %{y:.4f}'
        ),
        row=3, col=1
    )

    # Add vertical reference lines to distribution plot
    fig.add_trace(
        go.Scatter(
            x=[target_beta, target_beta],
            y=[0, 8],  # Arbitrary height that will be resized by autorange
            mode='lines',
            name=f'Target Beta ({target_beta})',
            line=dict(color='purple', width=2, dash='dash')
        ),
        row=3, col=1
    )

    fig.add_trace(
        go.Scatter(
            x=[beta_ols, beta_ols],
            y=[0, 8],  # Arbitrary height that will be resized by autorange
            mode='lines',
            name=f'Full Sample Beta ({beta_ols:.4f})',
            line=dict(color='black', width=2, dash='dot')
        ),
        row=3, col=1
    )

    # Add annotations with statistics to all plots
    # First plot annotations
    mean_unweighted = np.mean(unweighted_betas)
    std_unweighted = np.std(unweighted_betas)
    mean_exp_weighted = np.mean(exp_weighted_betas)
    std_exp_weighted = np.std(exp_weighted_betas)
    std_true = np.std(time_varying_betas)

    stats_text1 = (
        f"Target Correlation: {target_corr:.4f}<br>"
        f"Actual Correlation: {actual_corr:.4f}<br>"
        f"Full Sample R²: {r2_full:.4f}<br>"
        f"Expected R² (corr²): {target_corr ** 2:.4f}<br>"
        f"Target Beta: {target_beta:.4f}<br>"
        f"Actual Beta: {beta_ols:.4f}<br>"
        f"True Beta Std Dev: {std_true:.4f}"
    )

    fig.add_annotation(
        x=0.01, y=0.98,
        xref="paper", yref="paper",
        text=stats_text1,
        showarrow=False,
        font=dict(size=10),
        align="left",
        bgcolor="rgba(255, 255, 255, 0.8)",
        bordercolor="black",
        borderwidth=1,
        borderpad=6
    )

    # Second plot annotations
    stats_text2 = (
        f"Window Size: {window} days<br>"
        f"Exp. Weight Half-life: {half_life} days<br>"
        f"Mean R² (Unweighted): {mean_r2_unweighted:.4f}<br>"
        f"Mean R² (Exp. Weighted): {mean_r2_exp_weighted:.4f}<br>"
        f"Tracking Error (Unweighted): {unweighted_tracking_error:.4f}<br>"
        f"Tracking Error (Exp. Weighted): {exp_weighted_tracking_error:.4f}"
    )

    fig.add_annotation(
        x=0.01, y=0.65,
        xref="paper", yref="paper",
        text=stats_text2,
        showarrow=False,
        font=dict(size=10),
        align="left",
        bgcolor="rgba(255, 255, 255, 0.8)",
        bordercolor="black",
        borderwidth=1,
        borderpad=6
    )

    # Third plot annotations
    info_ratio_unweighted = abs(mean_unweighted - target_beta) / std_unweighted
    info_ratio_exp_weighted = abs(mean_exp_weighted - target_beta) / std_exp_weighted

    stats_text3 = (
        f"True Beta Std Dev: {std_true:.4f}<br>"
        f"Unweighted Std Dev: {std_unweighted:.4f}<br>"
        f"Exp. Weighted Std Dev: {std_exp_weighted:.4f}<br>"
        f"Unweighted Mean: {mean_unweighted:.4f}<br>"
        f"Exp. Weighted Mean: {mean_exp_weighted:.4f}<br>"
        f"Info Ratio (Unweighted): {info_ratio_unweighted:.4f}<br>"
        f"Info Ratio (Exp. Weighted): {info_ratio_exp_weighted:.4f}"
    )

    fig.add_annotation(
        x=0.01, y=0.3,
        xref="paper", yref="paper",
        text=stats_text3,
        showarrow=False,
        font=dict(size=10),
        align="left",
        bgcolor="rgba(255, 255, 255, 0.8)",
        bordercolor="black",
        borderwidth=1,
        borderpad=6
    )

    # Update subplot titles and layout
    fig.update_xaxes(title_text="Market Returns", row=1, col=1)
    fig.update_yaxes(title_text="Asset Returns", row=1, col=1)

    fig.update_xaxes(title_text="Window End Point", row=2, col=1)
    fig.update_yaxes(title_text="Beta", row=2, col=1)
    fig.update_yaxes(title_text="R-squared", secondary_y=True, row=2, col=1)

    fig.update_xaxes(title_text="Beta Value", row=3, col=1)
    fig.update_yaxes(title_text="Density", row=3, col=1)

    # Set y-axis range for R-squared
    fig.update_yaxes(range=[0, 1], secondary_y=True, row=2, col=1)

    # Update layout
    fig.update_layout(
        height=900,
        width=1000,
        title_text="Beta Estimation with Time-Varying Beta",
        title_x=0.5,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5
        ),
        margin=dict(l=60, r=30, t=120, b=60),
        hovermode="closest"
    )

    fig.show()
