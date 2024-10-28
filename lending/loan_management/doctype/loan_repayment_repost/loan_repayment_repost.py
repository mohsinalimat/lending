# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from lending.loan_management.doctype.loan_repayment.loan_repayment import calculate_amounts


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

		entries = frappe.get_all("Loan Repayment", filters, pluck="name", order_by="posting_date desc")
		for entry in entries:
			self.append("repayment_entries", {"loan_repayment": entry})

	def on_submit(self):
		self.trigger_on_cancel_events()
		self.trigger_on_submit_events()

	def trigger_on_cancel_events(self):
		for entry in self.get("repayment_entries"):
			repayment_doc = frappe.get_doc("Loan Repayment", entry.loan_repayment)
			repayment_doc.docstatus = 2
			repayment_doc.mark_as_unpaid()
			repayment_doc.update_demands(cancel=1)

			if repayment_doc.repayment_type in ("Advance Payment", "Pre Payment"):
				repayment_doc.cancel_loan_restructure()

			repayment_doc.make_gl_entries(cancel=1)

	def trigger_on_submit_events(self):
		for entry in self.get("repayment_entries"):
			repayment_doc = frappe.get_doc("Loan Repayment", entry.loan_repayment)
			repayment_doc.docstatus = 1
			repayment_doc.pending_principal_amount = 0
			repayment_doc.excess_amount = 0

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
			repayment_doc.allocate_amount_against_demands(amounts)

			# Run on_submit events
			repayment_doc.update_paid_amounts()
			repayment_doc.update_demands()
			repayment_doc.make_gl_entries()
