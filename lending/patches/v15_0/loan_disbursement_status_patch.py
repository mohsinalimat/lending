import frappe

loan_dis = frappe.db.get_all(
	"Loan Disbursement",
	filters={"status": ["!=", "Closed"]},
	fields=["name", "docstatus"],
)
for ld in loan_dis:
	if ld.docstatus == 0:
		frappe.db.set_value("Loan Disbursement", ld.name, "status", "Draft", update_modified=False)
	elif ld.docstatus == 1:
		frappe.db.set_value("Loan Disbursement", ld.name, "status", "Submitted", update_modified=False)
	elif ld.docstatus == 2:
		frappe.db.set_value("Loan Disbursement", ld.name, "status", "Cancelled", update_modified=False)

loan_repay_sche = frappe.db.get_all(
	"Loan Repayment Schedule",
	filters={"status": "Closed"},
	fields=["loan_disbursement"],
)
for lrs in loan_repay_sche:
	if lrs.loan_disbursement:
		frappe.db.set_value(
			"Loan Disbursement", lrs.loan_disbursement, "status", "Closed", update_modified=False
		)
