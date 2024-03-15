from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare

from odoo import fields, models, api, _


class StockConsumption(models.Model):
    _name = "stock.consumption"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = "Inventory Consumption"
    _order = "date desc, id desc"

    @api.model
    def _domain_location_id(self):
        if not self._is_inventory_mode():
            return
        return [('usage', 'in', ['internal', 'transit'])]

    @api.model
    def _domain_product_ids(self):
        if not self._is_inventory_mode():
            return
        return [('type', '=', 'product'), '|', ('company_id', '=', self.company_id), ('company_id', '=', False)]

    @api.model
    def _is_inventory_mode(self):
        """ Used to control whether a quant was written on or created during an
        "inventory session", meaning a mode where we need to create the stock. move
        record necessary to be consistent with the `inventory_quantity` field.
        """
        return self.env.context.get('inventory_mode') and self.user_has_groups('stock.group_stock_user')

    def _compute_has_account_moves(self):
        for consumption in self:
            if consumption.state in ['done', 'cancel'] and consumption.move_ids:
                account_move = self.env['account.move'].search_count([
                    ('stock_move_id.id', 'in', consumption.move_ids.ids)
                ])
                consumption.has_account_moves = account_move > 0
            else:
                consumption.has_account_moves = False

    def _set_view_context(self):
        """ Adds context when opening quants related views. """
        if not self.user_has_groups('stock.group_stock_multi_locations'):
            company_user = self.env.company
            warehouse = self.env['stock.warehouse'].search([('company_id', '=', company_user.id)], limit=1)
            if warehouse:
                self = self.with_context(default_location_id=warehouse.lot_stock_id.id, hide_location=True)

        # If user have rights to write on quant, we set quants in inventory mode.
        if self.user_has_groups('stock.group_stock_user'):
            self = self.with_context(inventory_mode=True)
        return self

    def _action_start(self):
        """ Confirms the Inventory Consumptions and generates its inventory lines
        if its state is draft and don't have already inventory lines (can happen
        with demo data or tests).
        """
        for consumption in self:
            if consumption.state != 'draft':
                continue
            vals = {'state': 'pending_approval', 'request_initiator_id': consumption.env.user.id}
            if not self.env["stock.consumption.lines"].search([('consumption_id', '=', consumption.id)]):
                data = {"product_ids": consumption.product_ids.ids, "location_ids": consumption.location_ids.ids,
                        "consumption_id": consumption.id, }
                consumption.create_consumption_lines(data)
            consumption.write(vals)

    state = fields.Selection(
        string='Status', default='draft', copy=False, index=True, readonly=True, tracking=True,
        selection=[('draft', 'Draft'), ('pending_approval', 'Awaiting Approval'),
                   ('confirm', 'Approved'), ('done', 'Validated'), ('cancel', 'Cancelled')])
    name = fields.Char('Reference', default="New", readonly=True, required=True,
                       states={'draft': [('readonly', False)]})
    date = fields.Datetime(
        'Inventory Date', required=True, default=fields.Datetime.now,
        help="If the inventory adjustment is not validated, date at which the theoretical quantities have been "
             "checked.\n If the inventory adjustment is validated, date at which the inventory adjustment has been "
             "validated.")
    request_initiator_id = fields.Many2one('res.users', 'Request Initiator', check_company=True, readonly=True,
                                           states={'draft': [('readonly', False)]})
    approver_id = fields.Many2one('res.users', 'Approver', check_company=True, readonly=True)

    product_ids = fields.Many2many('product.product', string='Products', check_company=True,
                                   domain=lambda self: self._domain_product_ids(),
                                   readonly=True, states={'draft': [('readonly', False)]},
                                   help="Specify Products to focus your consumption on particular Products.")

    company_id = fields.Many2one('res.company', 'Company', readonly=True, index=True, required=True,
                                 states={'draft': [('readonly', False)]}, default=lambda self: self.env.company)

    location_ids = fields.Many2one(
        'stock.location', string='Locations', index=True, readonly=True, check_company=True,
        states={'draft': [('readonly', False)]}, required=True, ondelete='restrict',
        domain=[('usage', 'in', ['internal'])])
    line_ids = fields.One2many(
        'stock.consumption.lines', 'consumption_id', string='Consumptions',
        copy=False, readonly=False,
        states={'done': [('readonly', True)]})
    move_ids = fields.One2many(
        'stock.move', 'consumption_id', string='Created Moves',
        states={'done': [('readonly', True)]})

    has_account_moves = fields.Boolean(string='Has Entries', compute='_compute_has_account_moves', store=False)

    analytic_account_id = fields.Many2one('account.analytic.account', string='Analytic Account')

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            vals['name'] = self.env['ir.sequence'].next_by_code('material.consumption.request') or _('New')
        return super(StockConsumption, self).create(vals)

    def action_start(self):
        self.ensure_one()
        self._action_start()
        res = self.action_view_consumptions_lines()
        return True

    def action_approve_request(self):
        self.ensure_one()
        self.write({'state': 'confirm', 'approver_id': self.env.user.id})

    def action_reject_request(self):
        self.ensure_one()
        self.write({'state': 'draft', 'approver_id': self.env.user.id})

    def action_view_consumptions_lines(self):
        """ Similar to _get_quants_action except specific for inventory adjustments (i.e. inventory counts). """
        self = self._set_view_context()
        ctx = dict(self.env.context or {})
        ctx['default_consumption_id'] = self.id
        action = {
            'name': 'Inventory Consumption',
            'view_mode': 'tree',
            'view_id': self.env.ref(
                'ts_advance_material_consumption.view_consumption_lines').id,
            'res_model': 'stock.consumption.lines',
            'type': 'ir.actions.act_window',
            'context': ctx,
            'domain': [('location_id.usage', 'in', ['internal', 'transit']),
                       ('consumption_id', '=', ctx['default_consumption_id'])],
            'help': """
                    <p class="o_view_nocontent_smiling_face">
                        {}
                    </p><p>
                        {} <span class="fa fa-long-arrow-right"/> {}</p>
                    """.format(_('Your stock is currently empty'),
                               _('Press the CREATE button to define quantity for each product in your stock or import them from a spreadsheet throughout Favorites'),
                               _('Import')),
        }
        return action

    @api.model
    def create_consumption_lines(self, vals):
        lines = []
        for location_id in vals['location_ids']:
            for product_id in vals['product_ids']:
                line = {
                    "consumption_id": self.id,
                    "product_id": product_id,
                    "location_id": location_id,
                    "quantity": self.env['product.product'].search([('id', '=', product_id)]).qty_available,
                }
                lines.append(line)
        self.env["stock.consumption.lines"].create(lines)

    @api.model
    def action_validate(self):
        move_vals = []
        if not self.user_has_groups('stock.group_stock_manager'):
            raise UserError(_('Only a stock manager can validate an inventory adjustment.'))
        if self.state != 'confirm':
            raise UserError(_(
                "You can't validate the consumption '%s', maybe this inventory "
                "has been already validated or isn't ready.", self.name))
        line_context = {'force_period_date': self.date}
        for line in self.line_ids:
            # Create and validate a move so that the quant matches its `inventory_quantity`.
            if float_compare(line.inventory_diff_quantity, 0,
                             precision_rounding=line.product_id.uom_id.rounding) > 0:
                move_vals.append(
                    line.with_context(**line_context)._get_inventory_move_values(
                        line.inventory_diff_quantity,
                        line.product_id.with_company(
                            line.company_id).consumption_location_id,
                        line.location_id,
                        consumption_id=self.id))
            else:
                move_vals.append(
                    line.with_context(**line_context)._get_inventory_move_values(
                        -line.inventory_diff_quantity,
                        line.location_id,
                        line.product_id.with_company(
                            line.company_id).consumption_location_id,
                        out=True,
                        consumption_id=self.id))
        moves = self.env['stock.move'].with_context(inventory_mode=False).create(move_vals)
        moves_done = moves._action_done()
        moves_done.mapped('move_line_ids').write({'date': self.date})
        moves_done.write({'date': self.date})
        # datetime.strptime(date, '%Y-%m-%d %H:%M:%S')
        if moves_done:
            self.state = 'done'

    def action_view_related_move_lines(self):
        self.ensure_one()
        domain = [('move_id', 'in', self.move_ids.ids)]
        action = {
            'name': _('Product Moves'),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.move.line',
            'view_type': 'list',
            'view_mode': 'list,form',
            'domain': domain,
        }
        return action

    def action_get_account_moves(self):
        self.ensure_one()
        action_ref = self.env.ref('account.action_move_journal_line')
        if not action_ref:
            return False
        action_data = action_ref.read()[0]
        action_data['domain'] = [('stock_move_id.id', 'in', self.move_ids.ids)]
        action_data['context'] = dict(self._context, create=False)
        return action_data

    def action_draft(self):
        for rec in self:
            rec.update({'state': 'draft'})

    def action_cancel_consumption(self):
        action = {}
        for rec in self:
            if rec.has_account_moves:
                moves_domain = [('move_id', 'in', rec.move_ids.ids)]
                move_lines = self.env['stock.move.line'].search(moves_domain)
                action = move_lines.action_revert_inventory()
            params = action.get('params', {})
            if params.get('type') == 'success':
                rec.update({'state': 'cancel'})
        return action

    @api.ondelete(at_uninstall=False)
    def _unlink_except_posted(self):
        for audit in self:
            if audit.state in ['done', 'cancel']:
                raise UserError(_('You cannot delete a validated record. Cancel it instead.'))
