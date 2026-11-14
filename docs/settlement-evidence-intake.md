# SYNPORA settlement-evidence intake

This document defines the minimum evidence needed to move from market-price proxies to auditable, realized farm economics. It is an intake specification only; it does not authorize hardware control, dispatch, trading, or investment.

## General rules

- Use UTC ISO-8601 timestamps with timezone offsets; retain the original provider/document timestamp and the ingestion timestamp separately.
- Preserve original invoices, statements, exports, and meter files unchanged. Store a SHA-256 checksum and a private evidence reference; never commit account numbers, wallet secrets, API keys, personal data, or unredacted invoices to Git.
- Record currency and units explicitly. Do not silently convert gross to net, kW to kWh, or BTC to fiat.
- A quote, offer, market snapshot, estimate, or model output is not a settlement.
- Every row must be traceable to a source document or authenticated provider export. Missing values stay blank; do not infer them.
- Reconcile period boundaries and timezone/DST handling before combining records.

## 1. GPU rental revenue and payouts

CSV header:

`period_start_utc,period_end_utc,provider,account_ref_redacted,hardware_model,gpu_count,gpu_hours,gross_amount,currency,fees_amount,tax_amount,net_paid_amount,payout_status,payout_timestamp_utc,source_observed_at_utc,source_document_ref,source_sha256,notes`

Count only completed and actually paid settlements as realized revenue. Keep booked, accrued, pending, canceled, and paid amounts distinct.

## 2. Bitcoin mining and pool settlements

CSV header:

`period_start_utc,period_end_utc,pool,worker_ref_redacted,algorithm,hashrate_average_ths,shares_accepted,shares_rejected,btc_gross,btc_fees,btc_net,fiat_conversion_amount,fiat_currency,conversion_timestamp_utc,payout_status,payout_tx_ref_redacted,payout_timestamp_utc,source_document_ref,source_sha256,notes`

Do not treat estimated hashprice, unconfirmed rewards, or dashboard estimates as realized payouts. If a fiat conversion is used, preserve the conversion source and timestamp separately.

## 3. Electricity, grid charges, and export settlements

CSV header:

`site_ref,period_start_utc,period_end_utc,meter_id_redacted,consumption_kwh,import_energy_amount,network_charges_amount,taxes_amount,fixed_fees_amount,total_import_invoice_amount,currency,export_kwh,export_settlement_amount,export_currency,export_settlement_status,invoice_date,settlement_date_utc,source_document_ref,source_sha256,notes`

Use all-in billed costs, including network charges, taxes, fixed fees, and other unavoidable charges. Keep import and export separate. A modeled tariff or spot-market quote is not an invoice or export settlement.

## 4. Capital expenditure and commissioning

CSV header:

`asset_ref,asset_category,manufacturer,model,quantity,invoice_date,commissioned_at_utc,purchase_amount,currency,shipping_amount,installation_amount,connection_amount,other_capex_amount,salvage_value_amount,source_document_ref,source_sha256,notes`

Include hardware, racks, networking, cooling, electrical installation, grid connection, batteries, and commissioning. Distinguish paid invoices from quotes and financing commitments.

## 5. Maintenance, operating costs, and downtime

CSV header:

`period_start_utc,period_end_utc,site_ref,asset_ref,cost_category,description,amount,currency,paid_status,paid_at_utc,downtime_start_utc,downtime_end_utc,downtime_reason,source_document_ref,source_sha256,notes`

Include repairs, replacement parts, cooling, connectivity, hosting, insurance, labor, service contracts, and other recurring costs. Record downtime separately from cost.

## 6. Battery and site-meter outcomes

CSV header:

`timestamp_utc,site_ref,meter_ref_redacted,battery_ref,charge_kwh,discharge_kwh,state_of_charge_pct,round_trip_efficiency_pct,grid_import_kwh,grid_export_kwh,measured_power_kw,measurement_interval_seconds,source_system,source_document_ref,source_sha256,notes`

Use measured meter/BMS values and identify measurement intervals. Simulated cycles, nominal capacity, and vendor specifications are not measured operating outcomes.

## Reconciliation and acceptance

Before any investment claim:

1. Reconcile each revenue period to paid provider/pool statements and bank or on-chain settlement evidence where applicable.
2. Reconcile electricity import and export to invoices, meter readings, and settled export statements for the same site and period.
3. Match capex and maintenance to paid invoices and asset records.
4. Resolve duplicate rows, overlapping periods, missing intervals, currency conversion timing, taxes, and refunds.
5. Calculate realized net cashflow only from reconciled records; report unverified or missing amounts separately.
6. Preserve source references and checksums so every aggregate can be audited.

Until these records exist and reconcile, `investment_gate.status` remains `not_investment_validated`. Forecast/dispatch empirical gates remain independent and blocked unless all four horizons separately meet their existing OOS-pair, MAE-skill, bootstrap, and selector requirements.
