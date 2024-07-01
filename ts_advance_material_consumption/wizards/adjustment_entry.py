from odoo import models, fields


class AdjustmentEntry(models.TransientModel):
    _name = 'adjustment.entry.wizard'

    def _compute_rec_id(self):
        self.ensure_one()
        self.record_ids = self._context.get('consumption_ids', None)

    account_id = fields.Many2one('account.account', string='Account')
    record_ids = fields.Many2many('stock.consumption', string="Consumption", compute='_compute_rec_id')

    def create_move(self):
        """Create and post accounting moves."""
        for record in self.record_ids:
            record.create_move(self.account_id.id)
