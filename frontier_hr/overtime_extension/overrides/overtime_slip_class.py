from hrms.hr.doctype.overtime_slip.overtime_slip import OvertimeSlip


class CustomOvertimeSlip(OvertimeSlip):
    def process_overtime_slip(self):
        """Skip Additional Salary creation — OT pay comes from salary structure formula."""
        pass
