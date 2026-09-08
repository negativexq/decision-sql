# Risk operations

Synthetic risk-monitoring operations with customers, accounts, transactions, risk assessments, alerts, alert events, investigations, cases, analyst actions, devices, logins, and watchlist matches.

Customer/account/transaction and alert/investigation paths are authorized; device-to-customer is deliberately not authorized. Latest risk assessment uses assessed timestamp and assessment ID as a tie-break. Stored event timestamps are UTC and the benchmark clock is fixed at 2026-06-30 UTC.

Cases cover open alerts, latest scores, high-risk JSON amounts, alert conversion, resolution time, device reuse, nested and correlated populations, NULL preservation, ranking, precision, authority traps, ambiguity, and policy.
