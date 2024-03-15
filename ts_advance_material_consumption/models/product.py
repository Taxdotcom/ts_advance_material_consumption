from odoo import models, fields


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def get_default_consumption_location(self):
        return self.env.ref('ts_advance_material_consumption.stock_location_material_consumption').id

    consumption_location_id = fields.Many2one(
        'stock.location', "Consumption Location", company_dependent=True, check_company=True,
        default=get_default_consumption_location,
        domain="[('usage', '=', 'inventory'), '|', ('company_id', '=', False), ('company_id', '=', allowed_company_ids[0])]",
        help="This stock location will be used, instead of the default one, as the source location for stock moves "
             "generated when you do an inventory consumption.")
