import { useCallback, useEffect, useState } from "react";

const ROLE_LABELS = {
  owner: "Owner",
  sourcing: "Sourcing",
  yard: "Yard",
  sales: "Sales",
  finance: "Finance",
  viewer: "Viewer",
};
const NAV_ITEMS = [
  ["overview", "Overview", "reports.read"],
  ["sourcing", "Sourcing", "suppliers.read"],
  ["inventory", "Inventory", "lots.read"],
  ["sales", "Sales", "sales.read"],
  ["reports", "Reports", "reports.read"],
  ["users", "Team", "users.manage"],
  ["audit", "Audit history", "users.manage"],
];
const PAGE_LABELS = {
  overview: "Overview",
  sourcing: "Sourcing & receiving",
  inventory: "Inventory",
  sales: "Sales & dispatch",
  reports: "Reports",
  users: "Team access",
  audit: "Audit history",
};
const formatVolume = (value) =>
  `${Number(value || 0).toLocaleString("en-KE", { maximumFractionDigits: 3 })} m³`;
const formatMoney = (value) =>
  new Intl.NumberFormat("en-KE", { style: "currency", currency: "KES", maximumFractionDigits: 0 }).format(value || 0);
const formatDate = (value) => (value ? new Date(value).toLocaleString() : "—");

async function request(path, { csrf, ...options } = {}) {
  const headers = { ...options.headers };
  if (options.body) headers["Content-Type"] = "application/json";
  if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = csrf || "";
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || body.error || `Request failed (${response.status}).`);
  return body;
}

function App() {
  const [session, setSession] = useState(null);
  const [page, setPage] = useState("overview");
  const [data, setData] = useState({});
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState(null);

  const notify = useCallback((message, error = false) => {
    setToast({ message, error });
    window.clearTimeout(notify.timeout);
    notify.timeout = window.setTimeout(() => setToast(null), 3200);
  }, []);

  const loadSession = useCallback(async () => {
    const result = await request("/api/v1/auth/session");
    setSession(result);
    setLoading(false);
    return result;
  }, []);

  useEffect(() => {
    loadSession().catch((error) => {
      notify(error.message, true);
      setLoading(false);
    });
  }, [loadSession, notify]);

  useEffect(() => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch((error) => {
        console.error("Could not register the installable app shell.", error);
      });
    }
  }, []);

  const can = useCallback((permission) =>
    session?.user?.role === "owner" || session?.permissions?.includes(permission), [session]);

  const loadPage = useCallback(async (nextPage = page) => {
    if (!session?.user) return;
    setPage(nextPage);
    try {
      const tasks = [];
      if (nextPage === "overview" || nextPage === "reports") {
        tasks.push(request("/api/v1/reports").then((value) => ["reports", value]));
      }
      if (["overview", "inventory", "sales", "sourcing"].includes(nextPage)) {
        tasks.push(request("/api/v1/lots").then((value) => ["lots", value]));
      }
      if (nextPage === "sourcing") {
        tasks.push(request("/api/v1/suppliers").then((value) => ["suppliers", value]));
      }
      if (nextPage === "sales" && can("sales.read")) {
        tasks.push(request("/api/v1/sales").then((value) => ["sales", value]));
      }
      if (nextPage === "users") {
        tasks.push(request("/api/v1/users").then((value) => ["users", value]));
      }
      if (nextPage === "audit") {
        tasks.push(request("/api/v1/audit").then((value) => ["audit", value]));
      }
      const loaded = await Promise.all(tasks);
      setData((current) => Object.fromEntries([...Object.entries(current), ...loaded]));
    } catch (error) {
      notify(error.message, true);
    }
  }, [can, notify, page, session]);

  useEffect(() => {
    if (session?.user) loadPage(page);
  }, [session?.user?.id, page, loadPage]);

  const submit = async (event, endpoint, message, refreshPage = page) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form).entries());
    if (endpoint === "/api/v1/lots") {
      if (!values.pieces) values.pieces = null;
      if (!values.dimensions) values.dimensions = null;
    }
    try {
      await request(endpoint, {
        method: "POST",
        csrf: session.csrf_token,
        body: JSON.stringify(values),
      });
      notify(message);
      form.reset();
      await loadPage(refreshPage);
    } catch (error) {
      notify(error.message, true);
    }
  };

  const signOut = async () => {
    try {
      await request("/api/v1/auth/session", { method: "DELETE", csrf: session.csrf_token });
      setData({});
      await loadSession();
    } catch (error) {
      notify(error.message, true);
    }
  };

  if (loading) {
    return <main className="auth-shell"><div className="auth-card">Loading TimberTrack…</div></main>;
  }
  if (!session?.user) {
    return <AuthScreen setup={session?.setup_required} csrf={session?.csrf_token} onSuccess={loadSession} notify={notify} toast={toast} />;
  }

  return (
    <>
      {toast && <div id="toast" className={`show${toast.error ? " error" : ""}`} role="status">{toast.message}</div>}
      <div className="app-shell">
        <aside className="sidebar">
          <a className="brand" href="#" onClick={(event) => event.preventDefault()}>
            <span className="brand-mark">T</span>
            <span>TimberTrack<small>YARD MANAGEMENT</small></span>
          </a>
          <nav>
            {NAV_ITEMS.filter(([, , permission]) => can(permission)).map(([id, label]) => (
              <button key={id} className={`nav-button${page === id ? " active" : ""}`} onClick={() => setPage(id)}>
                {label}
              </button>
            ))}
          </nav>
          <div className="sidebar-foot"><span className="online-dot" /> Your yard workspace</div>
        </aside>
        <section className="main-area">
          <header className="topbar">
            <div><p className="eyebrow">TIMBER OPERATIONS</p><h1>{PAGE_LABELS[page]}</h1></div>
            <div className="user-area"><span>{session.user.username} · {ROLE_LABELS[session.user.role]}</span><button className="button button-quiet" onClick={signOut}>Sign out</button></div>
          </header>
          <main className="content">
            <PageContent
              page={page}
              data={data}
              user={session.user}
              csrf={session.csrf_token}
              can={can}
              notify={notify}
              submit={submit}
              loadPage={loadPage}
            />
          </main>
        </section>
      </div>
      <Toast toast={toast} />
    </>
  );
}

function AuthScreen({ setup, csrf, onSuccess, notify, toast }) {
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setSubmitting(true);
    const form = Object.fromEntries(new FormData(event.currentTarget).entries());
    try {
      await request(setup ? "/api/v1/auth/setup" : "/api/v1/auth/login", {
        method: "POST",
        csrf,
        body: JSON.stringify(form),
      });
      await onSuccess();
    } catch (error) {
      notify(error.message, true);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <Toast toast={toast} />
      <main className="auth-shell">
        <form className="auth-card" onSubmit={handleSubmit}>
          <div className="brand auth-brand"><span className="brand-mark">T</span><span>TimberTrack<small>YARD MANAGEMENT</small></span></div>
          <h1>{setup ? "Set up your yard" : "Welcome back"}</h1>
          <p>{setup ? "Create your owner account. Use a strong password of at least 12 characters." : "Sign in to continue to your timber operations."}</p>
          <div className="field"><label htmlFor="username">Username</label><input id="username" name="username" autoComplete="username" minLength="3" maxLength="80" required /></div>
          <div className="field"><label htmlFor="password">Password</label><input id="password" name="password" type="password" autoComplete={setup ? "new-password" : "current-password"} minLength={setup ? "12" : "1"} required /></div>
          <button className="button button-primary" type="submit" disabled={submitting}>{submitting ? "Please wait…" : setup ? "Create owner account" : "Sign in"}</button>
        </form>
      </main>
    </>
  );
}

function Toast({ toast }) {
  return toast ? <div id="toast" className={`show${toast.error ? " error" : ""}`} role="status">{toast.message}</div> : null;
}

function Intro({ title, text, action }) {
  return <div className="page-intro"><div><h2>{title}</h2><p>{text}</p></div>{action}</div>;
}

function Stat({ title, value, note }) {
  return <div className="stat-card"><div className="stat-label">{title}</div><div className="stat-number">{value}</div><div className="stat-foot">{note}</div></div>;
}

function StatGrid({ children }) {
  return <div className="stats">{children}</div>;
}

function LotTable({ lots = [], canMove, moveLot, showActions = false }) {
  if (!lots.length) return <div className="empty">No timber lots yet. Record a receipt to start tracking stock.</div>;
  return (
    <div className="table-wrap">
      <table><thead><tr><th>Lot</th><th>Species / grade</th><th>Supplier</th><th>Available</th><th>Location</th><th>Status</th>{showActions && <th />}</tr></thead>
        <tbody>{lots.map((lot) => {
          const label = lot.lot_status === "reserved" ? "Reserved" : lot.status;
          return (
            <tr key={lot.id}>
              <td><button className="trace-button" onClick={() => moveLot.history(lot)}>{lot.lot_code}</button></td>
              <td>{lot.species}{lot.grade ? ` · ${lot.grade}` : ""}{lot.dimensions ? ` · ${lot.dimensions}` : ""}</td>
              <td>{lot.supplier_name}</td><td>{formatVolume(lot.remaining_m3)}</td><td>{lot.location}</td>
              <td><span className={`pill${label === "Sold" ? " sold" : label === "Partially sold" ? " warn" : ""}`}>{label}</span></td>
              {showActions && <td>{canMove && <button className="button button-quiet button-small" onClick={() => moveLot.move(lot)}>Move</button>}</td>}
            </tr>
          );
        })}</tbody>
      </table>
    </div>
  );
}

function LotForm({ suppliers, submit }) {
  return (
    <form className="card" onSubmit={(event) => submit(event, "/api/v1/lots", "Timber receipt recorded.", "sourcing")}>
      <div className="card-head"><div><h3>Receive timber</h3><p>Register source, grade, volume and yard location.</p></div></div>
      {!suppliers.length && <p className="warning">Add a supplier before recording a timber receipt.</p>}
      <div className="form-grid">
        <Field label="Lot code"><input name="lot_code" placeholder="e.g. LOT-2026-001" required /></Field>
        <Field label="Supplier" full><select name="supplier_id" required disabled={!suppliers.length}><option value="">Select a supplier</option>{suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}</select></Field>
        <Field label="Species"><input name="species" placeholder="e.g. Cypress" required /></Field>
        <Field label="Grade"><input name="grade" placeholder="e.g. C24" /></Field>
        <Field label="Dimensions"><input name="dimensions" placeholder="e.g. 50 × 100 mm" /></Field>
        <Field label="Pieces"><input name="pieces" type="number" min="1" step="1" /></Field>
        <Field label="Volume (m³)"><input name="volume_m3" type="number" min="0.001" step="0.001" required /></Field>
        <Field label="Cost per m³ (KES)"><input name="cost_per_m3" type="number" min="0" step="0.01" required /></Field>
        <Field label="Yard location" full><input name="location" placeholder="e.g. Bay A / Stack 04" required /></Field>
        <Field label="Origin document reference" full><input name="origin_document" placeholder="Permit or certificate reference" /></Field>
      </div>
      <div className="form-actions"><button className="button button-primary" disabled={!suppliers.length}>Record receipt</button></div>
    </form>
  );
}

function Field({ label, full = false, children }) {
  return <div className={`field${full ? " full" : ""}`}><label>{label}</label>{children}</div>;
}

function PageContent({ page, data, user, csrf, can, notify, submit, loadPage }) {
  const moveLot = {
    history: async (lot) => {
      try {
        const history = await request(`/api/v1/lots/${lot.id}/history`);
        window.alert(`${lot.lot_code} traceability\n\n${history.map((item) => `${formatDate(item.created_at)} · ${item.kind}: ${item.detail} · ${item.username}`).join("\n") || "No history yet."}`);
      } catch (error) { notify(error.message, true); }
    },
    move: async (lot) => {
      const location = window.prompt("Enter the new yard location:", lot.location);
      if (!location?.trim()) return;
      try {
        await request(`/api/v1/lots/${lot.id}/move`, { method: "POST", csrf, body: JSON.stringify({ location }) });
        notify("Stock location updated.");
        await loadPage("inventory");
      } catch (error) { notify(error.message, true); }
    },
  };

  if (page === "overview") {
    const report = data.reports || { summary: {}, stock_by_species: [] };
    const summary = report.summary;
    return <>
      <Intro title={`Good day, ${user.username}`} text="A quick look at your timber yard." action={<button className="button button-primary" onClick={() => loadPage("sourcing")}>+ Receive timber</button>} />
      <StatGrid>
        <Stat title="Available stock" value={formatVolume(summary.stock_m3)} note="Across all active lots" />
        <Stat title="Total lots" value={summary.lots || 0} note="Recorded in the yard" />
        <Stat title="Sold to date" value={formatVolume(summary.sold_m3)} note="Volume dispatched" />
        <Stat title={report.financial ? "Sales value" : "Workspace"} value={report.financial ? formatMoney(report.financial.sales_kes) : "Active"} note={report.financial ? "Recorded sales" : "Role-based access"} />
      </StatGrid>
      <div className="grid-two">
        <section className="card"><div className="card-head"><div><h3>Recent timber lots</h3><p>Latest receipts and current status</p></div><button className="button button-quiet button-small" onClick={() => loadPage("inventory")}>View stock</button></div><LotTable lots={(data.lots || []).slice(0, 6)} moveLot={moveLot} /></section>
        <section className="card"><div className="card-head"><div><h3>Stock by species</h3><p>Available volume in cubic metres</p></div></div>
          {report.stock_by_species?.length ? report.stock_by_species.map((row) => <div className="stock-row" key={row.species}><span>{row.species} <span className="muted">({row.lots} lots)</span></span><strong>{formatVolume(row.volume_m3)}</strong></div>) : <div className="empty">Species stock will appear here.</div>}
        </section>
      </div>
    </>;
  }

  if (page === "sourcing") {
    const suppliers = data.suppliers || [];
    return <>
      <Intro title="Source & receive" text="Capture suppliers, purchases, and traceable timber lots." />
      <div className="split">
        <section className="card"><div className="card-head"><div><h3>Recent receipts</h3><p>Each receipt creates a traceable stock lot.</p></div></div><LotTable lots={(data.lots || []).slice(0, 12)} moveLot={moveLot} /></section>
        <div>
          <form className="card" onSubmit={(event) => submit(event, "/api/v1/suppliers", "Supplier saved.", "sourcing")}>
            <div className="card-head"><div><h3>Add supplier</h3><p>Keep source contacts in one place.</p></div></div>
            <div className="form-grid"><Field label="Supplier name" full><input name="name" required /></Field><Field label="Phone" full><input name="phone" type="tel" /></Field></div>
            <div className="form-actions"><button className="button button-primary">Save supplier</button></div>
          </form>
          <LotForm suppliers={suppliers} submit={submit} />
        </div>
      </div>
    </>;
  }

  if (page === "inventory") {
    const lots = data.lots || [];
    return <>
      <Intro title="Yard inventory" text="Track availability, location, and movement for every lot." action={<button className="button button-primary" onClick={() => loadPage("sourcing")}>+ Receive timber</button>} />
      <StatGrid><Stat title="Available volume" value={formatVolume(lots.reduce((sum, lot) => sum + Number(lot.remaining_m3), 0))} note="Across all lots" /><Stat title="Active lots" value={lots.filter((lot) => Number(lot.remaining_m3) > 0).length} note="With stock available" /></StatGrid>
      <section className="card"><div className="card-head"><div><h3>All timber lots</h3><p>Select a lot code for its complete traceability history.</p></div></div><LotTable lots={lots} canMove={can("lots.move")} moveLot={moveLot} showActions /></section>
    </>;
  }

  if (page === "sales") {
    const lots = (data.lots || []).filter((lot) => Number(lot.remaining_m3) > 0);
    const sales = data.sales || [];
    return <>
      <Intro title="Sales & dispatch" text="Record customer sales against available lots; stock updates automatically." />
      <div className="split">
        <section className="card"><div className="card-head"><div><h3>Recent sales</h3><p>Latest dispatch records</p></div></div>
          {sales.length ? <DataTable columns={["Date", "Customer", "Lot / species", "Volume", "Price / m³"]} rows={sales.map((sale) => [formatDate(sale.created_at), sale.customer, `${sale.lot_code} · ${sale.species}`, formatVolume(sale.volume_m3), formatMoney(sale.price_per_m3)])} /> : <div className="empty">No sales recorded yet.</div>}
        </section>
        {can("sales.write") && <form className="card" onSubmit={(event) => submit(event, "/api/v1/sales", "Sale recorded and stock updated.", "sales")}>
          <div className="card-head"><div><h3>Record a sale</h3><p>Volume is deducted from the selected lot.</p></div></div>
          {!lots.length && <p className="warning">No available timber to sell. Record a receipt first.</p>}
          <div className="form-grid">
            <Field label="Customer" full><input name="customer" required /></Field>
            <Field label="Available lot" full><select name="lot_id" required disabled={!lots.length}><option value="">Select a lot</option>{lots.map((lot) => <option key={lot.id} value={lot.id}>{lot.lot_code} · {lot.species} ({formatVolume(lot.remaining_m3)} available)</option>)}</select></Field>
            <Field label="Volume (m³)"><input name="volume_m3" type="number" min="0.001" step="0.001" required /></Field>
            <Field label="Price per m³ (KES)"><input name="price_per_m3" type="number" min="0" step="0.01" required /></Field>
          </div>
          <div className="form-actions"><button className="button button-primary" disabled={!lots.length}>Record dispatch</button></div>
        </form>}
      </div>
    </>;
  }

  if (page === "reports") {
    const report = data.reports || { summary: {}, stock_by_species: [] };
    return <>
      <Intro title="Reports" text="Current stock, traceable timber volumes, and recorded sales." />
      <StatGrid>
        <Stat title="Available stock" value={formatVolume(report.summary.stock_m3)} note="Current balance" />
        <Stat title="Volume sold" value={formatVolume(report.summary.sold_m3)} note="Recorded dispatches" />
        <Stat title="Lots sourced" value={report.summary.lots || 0} note="All-time receipts" />
        <Stat title={report.financial ? "Sales value" : "Financial data"} value={report.financial ? formatMoney(report.financial.sales_kes) : "Restricted"} note={report.financial ? `Estimated cost: ${formatMoney(report.financial.estimated_cost_kes)}` : "Ask an owner for access"} />
      </StatGrid>
      <section className="card"><div className="card-head"><div><h3>Stock by species</h3><p>Remaining timber volume across all lots</p></div></div>{report.stock_by_species?.length ? <DataTable columns={["Species", "Open lots", "Available volume"]} rows={report.stock_by_species.map((row) => [row.species, row.lots, formatVolume(row.volume_m3)])} /> : <div className="empty">No stock data to report yet.</div>}</section>
    </>;
  }

  if (page === "users") {
    const users = data.users || [];
    const roles = Object.entries(ROLE_LABELS);
    return <>
      <Intro title="Team access" text="Create named accounts with role-based access to business records." />
      <div className="split">
        <section className="card"><div className="card-head"><div><h3>Team members</h3><p>Actions are recorded against each signed-in user.</p></div></div><DataTable columns={["Username", "Role", "Added"]} rows={users.map((item) => [item.username, ROLE_LABELS[item.role] || item.role, new Date(item.created_at).toLocaleDateString()])} /></section>
        <form className="card" onSubmit={(event) => submit(event, "/api/v1/users", "Team member added.", "users")}>
          <div className="card-head"><div><h3>Add team member</h3><p>Passwords must be at least 12 characters.</p></div></div>
          <div className="form-grid"><Field label="Username" full><input name="username" minLength="3" required /></Field><Field label="Temporary password" full><input name="password" type="password" minLength="12" required /></Field><Field label="Role" full><select name="role">{roles.map(([role, label]) => <option value={role} key={role}>{label}</option>)}</select></Field></div>
          <div className="form-actions"><button className="button button-primary">Create account</button></div>
        </form>
      </div>
    </>;
  }

  if (page === "audit") {
    const events = data.audit || [];
    return <><Intro title="Audit history" text="Recent account and inventory actions, attributed to the user who recorded them." />
      <section className="card"><div className="card-head"><div><h3>Latest activity</h3><p>Most recent 100 recorded events</p></div></div>
        {events.length ? <DataTable columns={["Time", "User", "Action", "Record", "Details"]} rows={events.map((item) => [formatDate(item.created_at), item.username, item.action, `${item.entity} #${item.entity_id}`, item.detail])} /> : <div className="empty">No recorded activity yet.</div>}
      </section>
    </>;
  }

  return null;
}

function DataTable({ columns, rows }) {
  return <div className="table-wrap"><table><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>
    {rows.map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>)}
  </tbody></table></div>;
}

export default App;
