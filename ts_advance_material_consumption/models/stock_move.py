from odoo import models, fields, _


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    def _get_revert_inventory_move_values(self):
        self.ensure_one()
        vals = {
            'name': _('%s [reverted]', self.reference),
            'product_id': self.product_id.id,
            'product_uom': self.product_uom_id.id,
            'product_uom_qty': self.qty_done,
            'company_id': self.company_id.id or self.env.company.id,
            'state': 'confirmed',
            'location_id': self.location_dest_id.id,
            'location_dest_id': self.location_id.id,
            'is_inventory': True,
            'consumption_id': self.move_id.consumption_id.id if self.move_id.consumption_id else '',
            'move_line_ids': [(0, 0, {
                'product_id': self.product_id.id,
                'product_uom_id': self.product_uom_id.id,
                'qty_done': self.qty_done,
                'location_id': self.location_dest_id.id,
                'location_dest_id': self.location_id.id,
                'company_id': self.company_id.id or self.env.company.id,
                'lot_id': self.lot_id.id,
                'package_id': self.package_id.id,
                'result_package_id': self.package_id.id,
                'owner_id': self.owner_id.id,
            })]
        }
        return vals


class StockMove(models.Model):
    _inherit = "stock.move"

    consumption_id = fields.Many2one('stock.consumption', 'Consumption')

    def _account_entry_move(self, qty, description, svl_id, cost):
        """ Accounting Valuation Entries """
        if not self.consumption_id:
            return super(StockMove, self)._account_entry_move(qty, description, svl_id, cost)

        accounting_date = self.consumption_id.date
        am_vals = []
        self.ensure_one()
        am_vals += (
            super(StockMove, self.with_context(force_period_date=accounting_date))._account_entry_move(qty, description,
                                                                                                       svl_id, cost))
        if self.product_id.type != 'product':
            # no stock valuation for consumable products
            return am_vals
        if self.restrict_partner_id and self.restrict_partner_id != self.company_id.partner_id:
            # if the move isn't owned by the company, we don't make any valuation
            return am_vals
        company_from = self._is_out() and self.mapped('move_line_ids.location_id.company_id') or False
        company_to = self._is_in() and self.mapped('move_line_ids.location_dest_id.company_id') or False

        # redirect to this expense account
        expense_account = self.product_id.categ_id.property_account_expense_categ_id.id
        journal_id, acc_src, acc_dest, acc_valuation = self._get_accounting_data_for_valuation()
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
        return am_vals
