/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { ConnectionLostError } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class AdkPosCart extends Component {
    static template = "adk_gestion_boutiques.PosCart";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        const params = this.props.action.params || {};
        this.sessionId = params.session_id || (this.props.action.context || {}).session_id;
        this.state = useState({
            data: null, loading: true, busy: false, search: "", categ: 0, cart: [],
            journalId: 0, partnerId: 0, tendered: "", done: null, orderId: 0, orderName: "", showPending: false,
            queue: this.readQueue(), syncing: false,
        });
        onWillStart(() => this.load());
        this._timer = setInterval(() => this.sync(), 10000);
        this._onOnline = () => this.sync();
        window.addEventListener("online", this._onOnline);
        onWillUnmount(() => {
            clearInterval(this._timer);
            window.removeEventListener("online", this._onOnline);
        });
    }

    async load() {
        const data = await this.orm.call("adk.shop.session", "pos_data", [this.sessionId]);
        this.state.data = data;
        const cash = data.journals.find((j) => j.type === "cash");
        if (!this.state.journalId) {
            this.state.journalId = (cash || data.journals[0] || { id: 0 }).id;
        }
        this.state.loading = false;
    }

    // ---------- mode hors connexion : les ventes attendent, puis se synchronisent ----------
    get queueKey() {
        return `adk_pos_queue_${this.sessionId}`;
    }

    readQueue() {
        try {
            return JSON.parse(window.localStorage.getItem(`adk_pos_queue_${this.sessionId}`) || "[]");
        } catch {
            return [];
        }
    }

    writeQueue() {
        try {
            window.localStorage.setItem(this.queueKey, JSON.stringify(this.state.queue));
        } catch {
            /* stockage indisponible : la file reste en mémoire */
        }
    }

    get queuedTotal() {
        return this.state.queue.reduce((a, q) => a + q.total, 0);
    }

    async sync() {
        if (this.state.syncing || !this.state.queue.length) {
            return;
        }
        this.state.syncing = true;
        try {
            for (const item of [...this.state.queue]) {
                if (item.failed) {
                    continue;
                }
                try {
                    const res = await this.orm.call("adk.shop.session", "pos_checkout", [
                        this.sessionId, item.lines, item.journalId, item.partnerId || false,
                        item.tendered || 0, false, item.orderId || false, item.uuid,
                    ]);
                    this.state.queue = this.state.queue.filter((q) => q.uuid !== item.uuid);
                    this.writeQueue();
                    const s = this.state.data.session;
                    s.live_cash = res.live_cash;
                    s.sales_count = res.sales_count;
                    s.sales_total = res.sales_total;
                    this.state.data.pending = res.pending;
                    this.notification.add(`${res.name} synchronisée — ${this.money(res.total)}`, { type: "success" });
                } catch (e) {
                    if (e instanceof ConnectionLostError) {
                        break; // toujours hors connexion : on réessaiera
                    }
                    item.failed = (e.data && e.data.message) || "Erreur";
                    this.writeQueue();
                    this.notification.add(`Vente non synchronisée : ${item.failed}`, { type: "danger", sticky: true });
                }
            }
        } finally {
            this.state.syncing = false;
        }
    }

    // ---------- formats ----------
    money(v) {
        const cur = this.state.data.currency;
        const n = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: cur.decimals, minimumFractionDigits: cur.decimals })
            .format(v || 0).replace(/[  ]/g, " ");
        return cur.position === "before" ? `${cur.symbol} ${n}` : `${n} ${cur.symbol}`;
    }

    initials(name) {
        return name.split(" ").filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
    }

    // ---------- catalogue ----------
    get products() {
        const q = this.state.search.trim().toLowerCase();
        return this.state.data.products.filter(
            (p) => (!this.state.categ || p.categ_id === this.state.categ) && (!q || p.name.toLowerCase().includes(q))
        );
    }

    productById(id) {
        return this.state.data.products.find((p) => p.id === id);
    }

    qtyOf(id) {
        const l = this.state.cart.find((x) => x.id === id);
        return l ? l.qty : 0;
    }

    // ---------- panier ----------
    add(p) {
        this.state.done = null;
        const l = this.state.cart.find((x) => x.id === p.id);
        if (l) {
            l.qty += 1;
        } else {
            this.state.cart.push({ id: p.id, qty: 1 });
        }
    }

    plus(line) {
        line.qty += 1;
    }

    minus(line) {
        line.qty -= 1;
        if (line.qty <= 0) {
            this.remove(line);
        }
    }

    remove(line) {
        this.state.cart = this.state.cart.filter((x) => x.id !== line.id);
    }

    clearCart() {
        this.state.cart = [];
        this.state.tendered = "";
        this.state.orderId = 0;
        this.state.orderName = "";
    }

    // ---------- commandes en attente (« Commander ») ----------
    get pending() {
        return this.state.data.pending || [];
    }

    loadPending(o) {
        this.state.cart = o.lines.map((l) => ({ id: l.product_id, qty: l.qty }));
        this.state.partnerId = o.partner_id || 0;
        this.state.orderId = o.id;
        this.state.orderName = o.name;
        this.state.showPending = false;
        this.state.done = null;
        this.state.tendered = "";
    }

    async cancelPending(o) {
        this.state.data.pending = await this.orm.call("adk.shop.session", "pos_cancel_order", [this.sessionId, o.id]);
        if (this.state.orderId === o.id) {
            this.clearCart();
        }
        this.notification.add(`${o.name} annulée`, { type: "warning" });
    }

    get canOrder() {
        return !this.state.busy && this.state.cart.length > 0;
    }

    async order() {
        if (!this.canOrder) {
            return;
        }
        this.state.busy = true;
        try {
            let res;
            try {
                res = await this.orm.call("adk.shop.session", "pos_order", [
                    this.sessionId,
                    this.state.cart.map((l) => ({ product_id: l.id, qty: l.qty })),
                    this.state.partnerId || false,
                    this.state.orderId || false,
                ]);
            } catch (e) {
                if (e instanceof ConnectionLostError) {
                    this.notification.add("Hors connexion : « Commander » nécessite le réseau. Utilisez « Encaisser » : la vente sera synchronisée plus tard.", { type: "warning" });
                    return;
                }
                throw e;
            }
            this.state.data.pending = res.pending;
            this.clearCart();
            this.state.partnerId = 0;
            this.state.done = { name: res.name, total: res.total, ordered: true };
            this.notification.add(`${res.name} enregistrée — en attente du paiement`, { type: "info" });
        } finally {
            this.state.busy = false;
        }
    }

    get cartLines() {
        return this.state.cart.map((l) => {
            const p = this.productById(l.id);
            return { ...l, p, total: p.price * l.qty };
        });
    }

    get itemCount() {
        return this.state.cart.reduce((a, l) => a + l.qty, 0);
    }

    get total() {
        return this.cartLines.reduce((a, l) => a + l.total, 0);
    }

    // ---------- paiement ----------
    get journal() {
        return this.state.data.journals.find((j) => j.id === this.state.journalId);
    }

    get isCash() {
        return this.journal && this.journal.type === "cash";
    }

    get tenderedValue() {
        return parseFloat(String(this.state.tendered).replace(/\s/g, "").replace(",", ".")) || 0;
    }

    get change() {
        return this.isCash && this.tenderedValue > this.total ? this.tenderedValue - this.total : 0;
    }

    get quickAmounts() {
        const t = this.total;
        if (!t) {
            return [];
        }
        const out = [Math.ceil(t)];
        for (const step of [1000, 5000, 10000, 20000, 50000]) {
            const v = Math.ceil(t / step) * step;
            if (!out.includes(v)) {
                out.push(v);
            }
        }
        return out.slice(0, 5);
    }

    setTendered(v) {
        this.state.tendered = String(v);
    }

    get canPay() {
        if (this.state.busy || !this.state.cart.length || !this.state.journalId) {
            return false;
        }
        if (this.isCash && this.state.tendered !== "" && this.tenderedValue + 1e-6 < this.total) {
            return false;
        }
        return true;
    }

    async pay(ticket) {
        if (!this.canPay) {
            return;
        }
        this.state.busy = true;
        const uuid = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
        const payload = {
            lines: this.state.cart.map((l) => ({ product_id: l.id, qty: l.qty })),
            journalId: this.state.journalId, partnerId: this.state.partnerId || 0,
            tendered: this.isCash ? this.tenderedValue : 0, orderId: this.state.orderId || 0,
            uuid, total: this.total,
        };
        try {
            let res;
            try {
                res = await this.orm.call("adk.shop.session", "pos_checkout", [
                    this.sessionId, payload.lines, payload.journalId, payload.partnerId || false,
                    payload.tendered, !!ticket, payload.orderId || false, uuid,
                ]);
            } catch (e) {
                if (!(e instanceof ConnectionLostError)) {
                    throw e;
                }
                // hors connexion : la vente est gardée sur cet appareil et sera synchronisée au retour du réseau
                this.state.queue.push(payload);
                this.writeQueue();
                for (const l of this.state.cart) {
                    const p = this.productById(l.id);
                    if (p && p.stock !== null) {
                        p.stock -= l.qty;
                    }
                }
                this.state.done = { name: "hors connexion", total: payload.total, change: this.change, offline: true };
                this.notification.add("Hors connexion : vente enregistrée sur cet appareil, synchronisation automatique au retour du réseau.", { type: "warning" });
                this.clearCart();
                this.state.partnerId = 0;
                return;
            }
            this.state.data.pending = res.pending;
            const change = this.change;
            for (const l of this.state.cart) {
                const p = this.productById(l.id);
                if (p && p.stock !== null) {
                    p.stock -= l.qty;
                }
            }
            const s = this.state.data.session;
            s.live_cash = res.live_cash;
            s.sales_count = res.sales_count;
            s.sales_total = res.sales_total;
            this.state.done = { name: res.name, total: res.total, change };
            this.notification.add(`${res.name} — ${this.money(res.total)} encaissé`, { type: "success" });
            this.clearCart();
            this.state.partnerId = 0;
            if (res.ticket) {
                await this.action.doAction(res.ticket);
            }
        } finally {
            this.state.busy = false;
        }
    }

    // ---------- navigation ----------
    async closeDay() {
        if (this.state.queue.length) {
            await this.sync();
            if (this.state.queue.length) {
                this.notification.add("Des ventes hors connexion ne sont pas encore synchronisées : rétablissez la connexion avant de clôturer.", { type: "danger" });
                return;
            }
        }
        const act = await this.orm.call("adk.shop.session", "action_close_wizard", [[this.sessionId]]);
        await this.action.doAction(act);
    }

    leave() {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "adk.shop", res_id: this.state.data.shop.id,
            views: [[false, "form"]], target: "current",
        });
    }
}

registry.category("actions").add("adk_shop_cart", AdkPosCart);
