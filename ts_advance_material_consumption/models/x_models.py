# -*- coding: utf-8 -*-

from odoo import models, fields, exceptions, _


class MaterialConsumptionExtension(models.Model):
    _inherit = 'stock.consumption'

    adjusted = fields.Boolean(default=True)
    expense_account_id = fields.Many2one('account.account', string='Adjustment Account')
    partner_id = fields.Many2one(
        'res.partner',
        string='Contact',
        auto_join=True,
        tracking=True,
        check_company=True,
    )
    op_type = fields.Selection(selection=[('', ''), ('personal', 'Direct Expense'), ('delay', 'Work in progress')],
                               string="Operation Type", default=False)

    def create_move(self, expense_account):
        """Create and post accounting moves."""
        self.ensure_one()
        if self.op_type in ['personal', False, ''] or self.adjusted:
            raise exceptions.UserError(_("Record is already adjusted."))
        stock_move_ids = self.move_ids
        account_move_ids = self.env['account.move'].search([('stock_move_id.id', 'in', stock_move_ids.ids)])

        # Iterate over existing account moves
        for move in account_move_ids:
            stock_move_id = move.stock_move_id
            # inv_val_acc, in_acc, out_acc = self.get_inventory_adjustment_accounts(move.stock_move_id.product_id)
            accounts_data = stock_move_id.product_id.product_tmpl_id.get_product_accounts()

            acc_src = stock_move_id._get_src_account(accounts_data)
            acc_dest = stock_move_id._get_dest_account(accounts_data)
            # expense_account = self.account_id.id

            journal_id = accounts_data.get('stock_journal', False).id
            qty = stock_move_id.product_uom_qty
            description = stock_move_id.reference
            svl_id = move.stock_valuation_layer_ids[0].id if move.stock_valuation_layer_ids else False
            line = move.line_ids.filtered(lambda l: l.account_id.id in [acc_src, acc_dest])
            cost = -1 * line[0].balance or 0.0

            company_from = stock_move_id._is_out() and stock_move_id.mapped(
                'move_line_ids.location_id.company_id') or False
            company_to = stock_move_id._is_in() and stock_move_id.mapped(
                'move_line_ids.location_dest_id.company_id') or False

            accounting_date = self.date or fields.Date.context_today(self)

            am_vals = []
            if stock_move_id._is_in():
                am_vals.append(
                    stock_move_id.with_context(
                        expense_account=expense_account, force_period_date=accounting_date).with_company(
                        company_to)._prepare_account_move_vals(expense_account, acc_src, journal_id, qty, description,
                                                               svl_id, cost))

            # Create Journal Entry for products leaving the company
            if stock_move_id._is_out():
                cost = -1 * cost
                am_vals.append(stock_move_id.with_context(expense_account=expense_account,
                                                          force_period_date=accounting_date).with_company(
                    company_from)._prepare_account_move_vals(
                    acc_dest, expense_account, journal_id, qty, description, svl_id, cost))

            if self.analytic_account_id:
                stock_move_id.add_key_value_to_line_ids(am_vals, expense_account, 'analytic_distribution',
                                                        {
                                                            str(self.analytic_account_id.id): 100.0,
                                                        })
            if am_vals:
                moves = self.env['account.move'].create(am_vals)
                moves.action_post()
                self.write({'adjusted': True})

    def action_adjustment_entry(self):
        self.ensure_one()
        action = self.env.ref('ts_advance_material_consumption.action_adjustment_entry_wizard')
        if not action or self.state == 'cancel':
            return False
        action = action.read()[0]
        action['context'] = dict(self._context, consumption_ids=[self.id])
        return action

    def action_all_adjustments(self):
        delayed_records = self.filtered(lambda rec: rec.op_type == 'delay')
        if not delayed_records or any(line.state == 'cancel' for line in delayed_records):
            raise exceptions.UserError(
                _("No records in 'delayed' operation type to post.Or there is a canceled record."))
        action = self.env.ref('ts_advance_material_consumption.action_adjustment_entry_wizard')
        if not action:
            return False
        action = action.read()[0]
        action['context'] = dict(self._context, consumption_ids=delayed_records.ids)
        return action


# MODULE B EXTENSION OF MODULE A
class StockMove(models.Model):
    _inherit = "stock.move"

    def filter_records_by_account_id(self, records, account_id):
        filtered_records = []

        for record in records:
            # Check if the account_id is present in the record or its lines
            if all(line[2].get('account_id') != account_id for line in record.get('line_ids', [])) \
                    and record.get('account_id') != account_id:
                filtered_records.append(record)

        return filtered_records

    def add_key_value_to_line_ids(self, data, account_id, key, value):
        for record in data:
            for index, line_id in enumerate(record.get('line_ids', [])):
                if line_id[2].get('account_id') == account_id:
                    new_line_id = (0, line_id[1], {**line_id[2], key: value})
                    record['line_ids'][index] = new_line_id
        return data

    def _account_entry_move(self, qty, description, svl_id, cost):
        """ Accounting Valuation Entries """
        if not self.consumption_id:
            return super(StockMove, self)._account_entry_move(qty, description, svl_id, cost)
        self.ensure_one()
        # redirect to this expense account
        consumption = self.consumption_id
        analytic_account_id = consumption.analytic_account_id

        accounting_date = consumption.date or fields.Date.context_today(self)

        expense_account = self.product_id.categ_id.property_account_expense_categ_id.id
        journal_id, acc_src, acc_dest, acc_valuation = self._get_accounting_data_for_valuation()
        am_vals = []
        am_vals += (super(StockMove, self)._account_entry_move(qty, description, svl_id, cost))
        if analytic_account_id:
            self.add_key_value_to_line_ids(am_vals, expense_account, 'analytic_distribution',
                                           {
                                               str(consumption.analytic_account_id.id): 100.0,
                                           })

        if consumption.op_type == 'delay':
            consumption.write({'adjusted': False})
            return self.filter_records_by_account_id(am_vals, expense_account)

        if consumption.op_type == 'personal' and consumption.expense_account_id:
            am_vals = self.filter_records_by_account_id(am_vals, expense_account)
            expense_account = self.consumption_id.expense_account_id.id

            if self.product_id.type != 'product' or \
                    (self.restrict_partner_id and self.restrict_partner_id != self.company_id.partner_id):
                return am_vals

            company_from = self._is_out() and self.mapped('move_line_ids.location_id.company_id') or False
            company_to = self._is_in() and self.mapped('move_line_ids.location_dest_id.company_id') or False
            # Create Journal Entry for products arriving in the company; in case of routes making the link between several
            # warehouse of the same company, the transit location belongs to this company, so we don't need to create accounting entries
            if self._is_in():
                am_vals.append(
                    self.with_context(expense_account=expense_account, force_period_date=accounting_date).with_company(
                        company_to)._prepare_account_move_vals(
                        expense_account, acc_src, journal_id, qty, description, svl_id, cost))

            # Create Journal Entry for products leaving the company
            if self._is_out():
                cost = -1 * cost
                am_vals.append(
                    self.with_context(expense_account=expense_account, force_period_date=accounting_date).with_company(
                        company_from)._prepare_account_move_vals(
                        acc_dest, expense_account, journal_id, qty, description, svl_id, cost))
        if analytic_account_id:
            self.add_key_value_to_line_ids(am_vals, expense_account, 'analytic_distribution',
                                           {str(consumption.analytic_account_id.id): 100.0})

        return am_vals
