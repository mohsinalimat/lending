# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt

from lending.loan_management.doctype.loan_repayment.loan_repayment import (
	calculate_amounts,
	get_pending_principal_amount,
)


class LoanRepaymentRepost(Document):
	def validate(self):
		self.get_repayment_entries()

	def get_repayment_entries(self):
		self.set("repayment_entries", [])
		filters = {
			"against_loan": self.loan,
			"docstatus": 1,
			"posting_date": (">=", self.repost_date),
		}

		if self.loan_disbursement:
			filters["loan_disbursement"] = self.loan_disbursement

		entries = frappe.get_all(
			"Loan Repayment", filters, pluck="name", order_by="posting_date desc, creation desc"
		)
		for entry in entries:
			self.append("repayment_entries", {"loan_repayment": entry})

	def on_submit(self):
		if self.clear_demand_allocation_before_repost:
			self.clear_demand_allocation()

		self.trigger_on_cancel_events()
		self.process_loan_demand()
		self.trigger_on_submit_events()

	def process_loan_demand(self):
		from lending.loan_management.doctype.loan_demand.loan_demand import reverse_demands

		if self.cancel_future_emi_demands:
			reverse_demands(self.loan, self.repost_date, demand_type="EMI")

		repayments = [d.loan_repayment for d in self.get("repayment_entries")]
		max_demand_date = frappe.db.get_value(
			"Loan Repayment", {"against_loan": self.loan, "name": ("in", repayments)}, "max(posting_date)"
		)

		frappe.flags.on_repost = True
		frappe.get_doc(
			{"doctype": "Process Loan Demand", "loan": self.loan, "posting_date": max_demand_date}
		).submit()
		frappe.flags.on_repost = False

	def clear_demand_allocation(self):
		demands = frappe.get_all(
			"Loan Demand",
			{
				"loan": self.loan,
				"docstatus": 1,
				"demand_type": "EMI",
				"demand_date": (">=", self.repost_date),
			},
			["name", "demand_amount"],
		)

		for demand in demands:
			frappe.db.set_value(
				"Loan Demand",
				demand.name,
				{
					"paid_amount": 0,
					"outstanding_amount": demand.demand_amount,
				},
			)

		for entry in self.get("repayment_entries"):
			repayment_doc = frappe.get_doc("Loan Repayment", entry.loan_repayment)
			for repayment_detail in repayment_doc.get("repayment_details"):
				if repayment_detail.demand_type == "EMI":
					frappe.delete_doc("Loan Repayment Detail", repayment_detail.name, force=1)

	def trigger_on_cancel_events(self):
		entries_to_cancel = [d.loan_repayment for d in self.get("entries_to_cancel")]
		for entry in self.get("repayment_entries"):
			repayment_doc = frappe.get_doc("Loan Repayment", entry.loan_repayment)
			if entry.loan_repayment in entries_to_cancel:
				repayment_doc.flags.ignore_links = True
				repayment_doc.flags.from_repost = True
				repayment_doc.cancel()
				repayment_doc.flags.from_repost = False
			else:
				repayment_doc.docstatus = 2

				repayment_doc.update_demands(cancel=1)
				repayment_doc.update_limits(cancel=1)
				repayment_doc.update_security_deposit_amount(cancel=1)

				if repayment_doc.repayment_type in ("Advance Payment", "Pre Payment"):
					repayment_doc.cancel_loan_restructure()

				# Delete GL Entries
				frappe.db.sql(
					"DELETE FROM `tabGL Entry` WHERE voucher_type='Loan Repayment' AND voucher_no=%s",
					repayment_doc.name,
				)

			filters = {"against_loan": self.loan, "docstatus": 1, "posting_date": ("<", self.repost_date)}

			if self.loan_disbursement:
				filters["loan_disbursement"] = self.loan_disbursement

			totals = frappe.db.get_value(
				"Loan Repayment",
				filters,
				[
					"SUM(principal_amount_paid) as total_principal_paid",
					"SUM(amount_paid) as total_amount_paid",
				],
			)

			frappe.db.set_value(
				"Loan",
				self.loan,
				{
					"total_principal_paid": totals.total_principal_paid,
					"total_amount_paid": totals.total_amount_paid,
				},
			)

			if self.loan_disbursement:
				frappe.db.set_value(
					"Loan Disbursement",
					self.loan_disbursement,
					"principal_amount_paid",
					totals.total_principal_paid,
				)

	def trigger_on_submit_events(self):
		from lending.loan_management.doctype.loan_demand.loan_demand import reverse_demands
		from lending.loan_management.doctype.loan_interest_accrual.loan_interest_accrual import (
			reverse_loan_interest_accruals,
		)
		from lending.loan_management.doctype.loan_repayment.loan_repayment import (
			update_installment_counts,
		)
		from lending.loan_management.doctype.loan_restructure.loan_restructure import (
			create_update_loan_reschedule,
		)

		entries_to_cancel = [d.loan_repayment for d in self.get("entries_to_cancel")]

		precision = cint(frappe.db.get_default("currency_precision")) or 2

		for entry in reversed(self.get("repayment_entries", [])):
			if entry.loan_repayment in entries_to_cancel:
				continue

			repayment_doc = frappe.get_doc("Loan Repayment", entry.loan_repayment)
			for entry in repayment_doc.get("repayment_details"):
				frappe.delete_doc("Loan Repayment Detail", entry.name, force=1)

			repayment_doc.docstatus = 1
			repayment_doc.set("pending_principal_amount", 0)
			repayment_doc.set("excess_amount", 0)

			charges = []
			if self.get("payable_charges"):
				charges = [d.get("charge_code") for d in self.get("payable_charges")]

			amounts = calculate_amounts(
				repayment_doc.against_loan,
				repayment_doc.posting_date,
				payment_type=repayment_doc.repayment_type,
				charges=charges,
				loan_disbursement=repayment_doc.loan_disbursement,
				for_update=True,
			)

			repayment_doc.set_missing_values(amounts)

			loan = frappe.get_doc("Loan", repayment_doc.against_loan)
			pending_principal_amount = get_pending_principal_amount(
				loan, loan_disbursement=self.loan_disbursement
			)

			repayment_doc.set("pending_principal_amount", flt(pending_principal_amount, precision))
			repayment_doc.before_validate()

			repayment_doc.allocate_amount_against_demands(amounts)

			if repayment_doc.repayment_type in ("Advance Payment", "Pre Payment"):
				create_update_loan_reschedule(
					repayment_doc.against_loan,
					repayment_doc.posting_date,
					repayment_doc.name,
					repayment_doc.repayment_type,
					repayment_doc.principal_amount_paid,
					loan_disbursement=repayment_doc.loan_disbursement,
				)

				repayment_doc.process_reschedule()

			# Run on_submit events
			repayment_doc.update_paid_amounts()
			repayment_doc.update_demands()
			repayment_doc.update_limits()
			repayment_doc.update_security_deposit_amount()
			repayment_doc.db_update_all()
			repayment_doc.make_gl_entries()

			update_installment_counts(self.loan)

		if self.cancel_future_penal_accruals_and_demands:
			reverse_loan_interest_accruals(
				self.loan,
				self.repost_date,
				interest_type="Penal Interest",
				is_npa=0,
				on_payment_allocation=False,
			)

			reverse_demands(self.loan, add_days(self.repost_date, 1), demand_type="Penalty")
