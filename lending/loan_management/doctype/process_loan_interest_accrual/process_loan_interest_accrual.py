# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
import erpnext
from frappe.model.document import Document
from frappe.utils import add_days, nowdate

from lending.loan_management.doctype.loan_interest_accrual.loan_interest_accrual import (
	make_accrual_interest_entry_for_loans,
)


class ProcessLoanInterestAccrual(Document):
	def on_submit(self):
		make_accrual_interest_entry_for_loans(
			self.posting_date,
			self.name,
			loan=self.loan,
			loan_product=self.loan_product,
			accrual_type=self.accrual_type,
			accrual_date=self.posting_date,
		)

def get_loan_accrual_frequency(company):
	company_doc = frappe.qb.DocType("Company")
	query = (
		frappe.qb.from_(company_doc)
		.select(company_doc.loan_accrual_frequency)
		.where(company_doc.name == company)
	)
	loan_accrual_frequency = query.run(as_dict=True)[0]['loan_accrual_frequency']
	return loan_accrual_frequency

def is_posting_date_accrual_day(company, posting_date):
	loan_accrual_frequency = get_loan_accrual_frequency(company)
	day_of_the_month = frappe.utils.getdate(posting_date).day
	weekday = frappe.utils.getdate(posting_date).weekday()
	match loan_accrual_frequency:
		case "Daily":
			return True
		case "Weekly":
			if weekday == 0:
				return True
		case "Fortnightly":
			# More thinking required
			# May or may not work
			# The logic for week_of_the_month assumes it's Monday, so should only be used
			# in this specific circumstance
			week_of_the_month = ((day_of_the_month - 1) // 7) % 2
			if weekday == 0 and (week_of_the_month == 1 or week_of_the_month == 3):
				return True
			pass
		case "Monthly":
			if day_of_the_month == 1:
				return True
	return False



def schedule_accrual():
	company = erpnext.get_default_company()
	posting_date = add_days(nowdate(), -1)
	if is_posting_date_accrual_day(company=company, post_date=posting_date):
		process_loan_interest_accrual_for_loans(posting_date=posting_date)

def process_loan_interest_accrual_for_loans(
	posting_date=None, loan_product=None, loan=None, accrual_type="Regular"
):
	loan_process = frappe.new_doc("Process Loan Interest Accrual")
	loan_process.posting_date = posting_date or add_days(nowdate(), -1)
	loan_process.loan_product = loan_product
	loan_process.loan = loan
	loan_process.accrual_type = accrual_type

	loan_process.submit()

	return loan_process.name
