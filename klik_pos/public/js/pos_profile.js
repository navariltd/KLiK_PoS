frappe.ui.form.on('POS Profile', {
    hide_images(frm) {
        if (!frm.doc.custom_default_view) {
            frm.set_value('custom_default_view', frm.doc.hide_images ? 'List View' : 'Grid View');
        }
    },

    refresh(frm) {
        frm.add_custom_button(__('Install Walk-in Party Fields'), () => {
            frappe.confirm(
                __('Add Walk-in Name / Tax ID / Phone fields to Quotation, Sales Order, Delivery Note and Sales Invoice?'),
                () => {
                    frappe.call({
                        method: 'klik_pos.setup.walkin_fields.install_walkin_party_fields',
                        freeze: true,
                        freeze_message: __('Installing walk-in fields...'),
                        callback: (r) => {
                            if (!r.exc) {
                                frappe.show_alert({
                                    message: __('Walk-in fields installed on: {0}',
                                        [(r.message.installed_on || []).join(', ')]),
                                    indicator: 'green',
                                });
                            }
                        },
                    });
                }
            );
        }, __('Setup'));
    }
});