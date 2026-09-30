BOND_OAS_PCTILE_SQL = '''
SELECT
	*
FROM
	(
	SELECT
		asof_date,
		isin_number,
		min(avg(oas)) OVER(PARTITION BY isin_number) AS oas_min,
		avg(oas) AS oas,
		max(avg(oas)) OVER(PARTITION BY isin_number) AS oas_max,
		100 * percent_rank() OVER(PARTITION BY isin_number
	ORDER BY
		avg(oas)) AS oas_pctile,
		RANK() OVER(PARTITION BY isin_number
	ORDER BY
		asof_date DESC) AS rk
	FROM
		ice_index_all
	WHERE
		asof_date BETWEEN '{}' AND '{}'
		AND ice_index_ticker IN {}
		AND frequency = 'DAILY'
	GROUP BY
		asof_date,
		isin_number
) t1
WHERE
	t1.rk = 1
ORDER BY
	asof_date DESC
'''

LOAN_OAS_PCTILE_SQL = '''
SELECT
	*
FROM
	(
	SELECT
		asof_date,
		loanx,
		min(avg(fl_mat_dm)) OVER(PARTITION BY loanx) AS oas_min,
		avg(fl_mat_dm) AS oas,
		max(avg(fl_mat_dm)) OVER(PARTITION BY loanx) AS oas_max,
		100 * percent_rank() OVER(PARTITION BY loanx
	ORDER BY
		avg(fl_mat_dm)) AS oas_pctile,
		RANK() OVER(PARTITION BY loanx
	ORDER BY
		asof_date DESC) AS rk
	FROM
		jpm_loan_index
	WHERE
		asof_date BETWEEN '{}' AND '{}'
		AND jpm_index_ticker IN ('USD', 'Non-USD')
	GROUP BY
		asof_date,
		loanx
) t1
WHERE
	t1.rk = 1
ORDER BY
	asof_date DESC
'''

LOAN_RISK_SQL = '''
SELECT
	loanx,
	avg(end_price) AS price,
	avg(end_cpn_rate) AS cpn_rate,
	avg(end_curr_yld*100) AS curr_yld,
	avg(fl_mat_dm) AS fl_mat_dm,
	avg(fw_mat_dm) AS fw_mat_dm,	
	avg(fl_3y_dm) AS fl_3y_dm,
	avg(fw_3y_dm) AS fw_3y_dm,
	avg(fl_mat_yld*100) AS fl_mat_yld,
	avg(fw_mat_yld*100) AS fw_mat_yld,	
	avg(fl_3y_yld*100) AS fl_3y_yld,
	avg(fw_3y_yld*100) AS fw_3y_yld,
	avg(fl_mat_moddur) AS fl_mat_moddur,
	avg(fw_mat_moddur) AS fw_mat_moddur,	
	avg(fl_3y_moddur) AS fl_3y_moddur,
	avg(fw_3y_moddur) AS fw_3y_moddur
FROM
	jpm_loan_index
WHERE
	asof_date = '{}'
GROUP BY
	loanx
'''
