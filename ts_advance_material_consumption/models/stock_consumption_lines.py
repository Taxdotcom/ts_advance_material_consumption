from odoo import fields, models, api, _


class ConsumptionLines(models.Model):
    _name = 'stock.consumption.lines'
    _description = "Consumption Lines"
    _order = "id desc"

    @api.model
    def _domain_location_id(self):
        return self.env["stock.consumption"]._domain_location_id()

    def _default_location_id(self):
        for rec in self:
            return rec.consumption_id.location_id

    def _domain_product_id(self):
        return self.env["stock.quant"]._domain_product_id()

    def _domain_lot_id(self):
        return self.env["stock.quant"]._domain_lot_id()

    @api.depends('inventory_quantity')
    def _compute_consumption_qty_set(self):
        for line in self:
            line.consumption_qty_set = True

    @api.depends('consume_qty', 'quantity', 'inventory_quantity')
    def _compute_inventory_quantity(self):
        for line in self:
            line.inventory_quantity = line.quantity - line.consume_qty

    @api.depends('quantity', 'product_id')
    def _compute_product_quantity(self):
        for line in self:
            line.quantity = line.product_id.qty_available

    @api.depends('inventory_quantity')
    def _compute_consumption_diff_quantity(self):
        for line in self:
            line.inventory_diff_quantity = line.inventory_quantity - line.quantity

    def _get_inventory_move_values(self, qty, location_id, location_dest_id, out=False, consumption_id=False):
        """ Called when user manually set a new quantity (via `inventory_quantity`)
        just before creating the corresponding stock move.

        :param location_id: `stock.location`
        :param location_dest_id: `stock.location`
        :param consumption_id: 'stock.consumption'
        :param out: boolean to set on True when the move go to inventory adjustment location.
        :return: dict with all values needed to create a new `stock.move` with its move line.
        """
        self.ensure_one()
        if self.env.context.get('force_period_date'):
            date = self.env.context.get('force_period_date')
        else:
            date = fields.Date.context_today(self)
        if fields.Float.is_zero(qty, 0, precision_rounding=self.product_uom_id.rounding):
            name = _('Product Quantity Confirmed')
        else:
            name = _('Product Quantity Updated')
        return {
            'name': self.env.context.get('inventory_name') or name,
            'product_id': self.product_id.id,
            'product_uom': self.product_uom_id.id,
            'product_uom_qty': qty,
            'company_id': self.company_id.id or self.env.company.id,
            'state': 'confirmed',
            'location_id': location_id.id,
            'location_dest_id': location_dest_id.id,
            'is_inventory': True,
            'date': date,
            'move_line_ids': [(0, 0, {
                'product_id': self.product_id.id,
                'product_uom_id': self.product_uom_id.id,
                'qty_done': qty,
                'date': date,
                'location_id': location_id.id,
                'location_dest_id': location_dest_id.id,
                'company_id': self.company_id.id or self.env.company.id,
                'lot_id': self.lot_id.id or False,
                'package_id': out and self.package_id.id or False,
                'result_package_id': (not out) and self.package_id.id or False,
                'owner_id': self.owner_id.id or False,
            })],
            'consumption_id': consumption_id
        }

    consume_qty = fields.Float('Consumption Qty')
    consumption_id = fields.Many2one('stock.consumption', string='Consumption Id')
    date = fields.Datetime(related='consumption_id.date')
    state = fields.Selection(related='consumption_id.state', string='State',
                             selection=[('draft', 'Draft'), ('cancel', 'Cancelled'), ('confirm', 'In Progress'),
                                        ('done', 'Validated')])
    company_id = fields.Many2one(related='location_id.company_id', string='Company', store=True, readonly=True)
    location_id = fields.Many2one('stock.location', 'Location', related='consumption_id.location_ids', readonly=True)
    product_id = fields.Many2one(
        'product.product', 'Product',
        domain=lambda self: self._domain_product_id(),
        ondelete='restrict', required=True, index=True, check_company=True)
    product_uom_id = fields.Many2one(
        'uom.uom', 'Unit of Measure',
        readonly=True, related='product_id.uom_id')
    quantity = fields.Float(
        'Quantity', compute='_compute_product_quantity',
        help='Quantity of products in this line, in the default unit of measure of the product',
        readonly=True, digits='Product Unit of Measure')
    inventory_quantity = fields.Float(
        'Counted Quantity', digits='Product Unit of Measure', compute='_compute_inventory_quantity',
        help="The product's counted quantity.", readonly=True)
    inventory_diff_quantity = fields.Float(
        'Difference', compute='_compute_consumption_diff_quantity', store=True,
        help="Indicates the gap between the product's theoretical quantity and its counted quantity.",
        readonly=True, digits='Product Unit of Measure')
    consumption_qty_set = fields.Boolean(store=True, compute='_compute_consumption_qty_set', readonly=False,
                                         default=False)
    lot_id = fields.Many2one(
        'stock.lot', 'Lot/Serial Number', index=True,
        ondelete='restrict', check_company=True,
        domain=lambda self: self._domain_lot_id())
    owner_id = fields.Many2one(
        'res.partner', 'Owner',
        help='This is the owner of the quant', check_company=True)
    package_id = fields.Many2one(
        'stock.quant.package', 'Package',
        domain="[('location_id', '=', location_id)]",
        help='The package containing this quant', ondelete='restrict', check_company=True)

    @api.model
    def action_apply_consumption(self, vals):
        """
            Triggers for multiple consumption lines at the same time
        """
        for line in self.browse(vals):
            line.consumption_id.action_validate()
            self.consumption_qty_set = False
            return True
