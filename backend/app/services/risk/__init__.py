"""Portfolio risk analytics: covariance and risk decomposition, VaR/ES with
backtests, factor exposures, stress tests, causal stress propagation and trade
counterfactuals.

Every analysis reads one source: yfinance auto-adjusted (total return) daily
closes cached under interval "1d_yf", USD/CAD included (Yahoo CAD=X), so
holdings, factors and drivers share one calendar and one adjustment basis.
The modules below the service are pure numpy over a `data.Panel`.
"""
