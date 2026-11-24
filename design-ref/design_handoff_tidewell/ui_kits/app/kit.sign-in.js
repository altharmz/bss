const { Button, IconButton, Icon, Avatar, Badge, Tag, Card, Input, Select, Checkbox, Radio, Switch, Tabs, Dialog, Toast, Tooltip } = window.TidewellDesignSystem_8a4ccf;
function SignIn({ onSignIn }) {
  const [email, setEmail] = React.useState("maya@northwind.studio");
  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 32, background: "var(--surface-page)" }}>
      <div style={{ width: "100%", maxWidth: 380, display: "flex", flexDirection: "column", gap: 24 }}>
        <div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 28, letterSpacing: "-0.5px", color: "var(--text-strong)", marginBottom: 28 }}>Tidewell</div>
          <h1 style={{ margin: 0, fontFamily: "var(--font-display)", fontWeight: 400, fontSize: 44, lineHeight: 1.05, letterSpacing: "-0.88px", color: "var(--text-strong)" }}>Welcome back</h1>
          <p style={{ margin: "10px 0 0", fontSize: 15, color: "var(--text-muted)" }}>Sign in to see what your team planned this week.</p>
        </div>
        <form onSubmit={e => { e.preventDefault(); onSignIn(); }} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <Input label="Work email" type="email" value={email} onChange={e => setEmail(e.target.value)} />
          <Input label="Password" type="password" defaultValue="tidewell" />
          <Button type="submit" size="lg" fullWidth>Sign in</Button>
          <Button variant="outline" size="lg" fullWidth iconLeft={<Icon name="mail" />}>Email me a sign-in link</Button>
        </form>
        <p style={{ margin: 0, fontSize: 13, color: "var(--text-muted)" }}>New to Tidewell? <a href="#">Create a workspace</a></p>
      </div>
    </div>
  );
}
Object.assign(window, { SignIn });
