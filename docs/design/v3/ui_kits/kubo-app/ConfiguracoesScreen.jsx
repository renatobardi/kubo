// Configurações — tela de ajustes do dono (padrão de mercado: rail + painel).
const K = window.KoboDesignSystem_6efae6;
const { useState } = React;

function Segmented({ options, value, onChange }) {
  return (
    <div style={{ display: 'inline-flex', padding: 3, gap: 2, background: 'var(--muted)', borderRadius: 'var(--radius-4xl)' }}>
      {options.map(o => {
        const active = o.value === value;
        return (
          <button key={o.value} onClick={() => onChange(o.value)}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '5px 12px', border: 'none', cursor: 'pointer',
              borderRadius: 'var(--radius-4xl)', fontSize: 13, fontFamily: 'var(--font-sans)', fontWeight: active ? 500 : 400,
              background: active ? 'var(--background)' : 'transparent', color: active ? 'var(--foreground)' : 'var(--muted-foreground)',
              boxShadow: active ? '0 0 0 1px color-mix(in oklab, var(--foreground) 8%, transparent)' : 'none' }}>
            {o.icon && <K.Icon name={o.icon} size={14} />}{o.label}
          </button>
        );
      })}
    </div>
  );
}

function Field({ label, children, hint }) {
  const { Label } = K;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <Label>{label}</Label>
      {children}
      {hint && <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>{hint}</span>}
    </div>
  );
}

function Row({ title, desc, control }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
      <div style={{ minWidth: 0 }}>
        <p style={{ margin: 0, fontSize: 14, fontWeight: 500, color: 'var(--foreground)' }}>{title}</p>
        {desc && <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--muted-foreground)' }}>{desc}</p>}
      </div>
      <div style={{ flexShrink: 0 }}>{control}</div>
    </div>
  );
}

function ConfiguracoesScreen({ dark, onToggleDark }) {
  const { PageHeader, Card, CardHeader, CardTitle, CardDescription, CardContent, Input, Button, Switch, Select, Badge, Icon } = K;
  const owner = window.KUBO_DATA.owner;
  const [cat, setCat] = useState('Perfil');
  const [theme, setTheme] = useState(dark ? 'escuro' : 'claro');

  const setThemeMode = (m) => {
    setTheme(m);
    if (m === 'sistema') onToggleDark(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    else onToggleDark(m === 'escuro');
  };

  const cats = [
    { id: 'Perfil', icon: 'user' },
    { id: 'Aparência', icon: 'sun' },
    { id: 'Notificações', icon: 'bell' },
    { id: 'Segurança', icon: 'lock' },
    { id: 'Idioma & região', icon: 'globe' },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, padding: 24 }}>
      <PageHeader title="Configurações" description="Preferências da sua conta e do ateliê." />
      <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: 24, alignItems: 'start' }}>
        {/* Rail */}
        <nav style={{ display: 'flex', flexDirection: 'column', gap: 2, position: 'sticky', top: 0 }}>
          {cats.map(c => {
            const active = c.id === cat;
            return (
              <button key={c.id} onClick={() => setCat(c.id)}
                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', border: 'none', cursor: 'pointer', textAlign: 'left',
                  borderRadius: 'var(--radius-lg)', fontSize: 14, fontFamily: 'var(--font-sans)', fontWeight: active ? 500 : 400,
                  background: active ? 'var(--muted)' : 'transparent', color: active ? 'var(--foreground)' : 'var(--muted-foreground)' }}
                onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = 'color-mix(in oklab, var(--muted) 50%, transparent)'; }}
                onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent'; }}>
                <Icon name={c.icon} size={16} style={{ color: active ? 'var(--foreground)' : 'var(--muted-foreground)' }} /> {c.id}
              </button>
            );
          })}
        </nav>

        {/* Panel */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
          {cat === 'Perfil' && (
            <Card>
              <CardHeader><CardTitle>Perfil</CardTitle><CardDescription>Como você aparece no ateliê.</CardDescription></CardHeader>
              <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 56, height: 56, borderRadius: 9999, background: 'var(--primary)', color: 'var(--primary-foreground)', fontSize: 22, fontWeight: 600 }}>{owner.name.charAt(0)}</div>
                  <Button variant="outline" size="sm">Trocar avatar</Button>
                </div>
                <Field label="Nome"><Input defaultValue={owner.name} /></Field>
                <Field label="Email" hint="Usado para entrar e receber envios.">
                  <div style={{ display: 'flex', gap: 8 }}>
                    <Input defaultValue={owner.email} disabled style={{ flex: 1, color: 'var(--muted-foreground)' }} />
                    <Button variant="outline">Alterar</Button>
                  </div>
                </Field>
                <div style={{ display: 'flex', justifyContent: 'flex-end' }}><Button>Salvar alterações</Button></div>
              </CardContent>
            </Card>
          )}

          {cat === 'Aparência' && (
            <Card>
              <CardHeader><CardTitle>Aparência</CardTitle><CardDescription>Tema da interface.</CardDescription></CardHeader>
              <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <Row title="Tema" desc="Claro, escuro ou seguir o sistema."
                  control={<Segmented value={theme} onChange={setThemeMode}
                    options={[{ value: 'claro', label: 'Claro', icon: 'sun' }, { value: 'escuro', label: 'Escuro', icon: 'moon' }, { value: 'sistema', label: 'Sistema', icon: 'monitor' }]} />} />
              </CardContent>
            </Card>
          )}

          {cat === 'Notificações' && (
            <Card>
              <CardHeader><CardTitle>Notificações</CardTitle><CardDescription>Quando o Kubo deve te avisar.</CardDescription></CardHeader>
              <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <Row title="Gates aguardando você" desc="Uma decisão travando um flow." control={<Switch defaultChecked />} />
                <div style={{ height: 1, background: 'var(--border)' }} />
                <Row title="Falhas de execução" desc="Quando um worker falha." control={<Switch defaultChecked />} />
                <div style={{ height: 1, background: 'var(--border)' }} />
                <Row title="Resumo diário no Telegram" desc="Digest das 8h." control={<Switch />} />
              </CardContent>
            </Card>
          )}

          {cat === 'Segurança' && (
            <>
              <Card>
                <CardHeader><CardTitle>Segurança</CardTitle><CardDescription>Acesso à sua conta.</CardDescription></CardHeader>
                <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  <Row title="Senha" desc="Troque periodicamente para manter a conta segura." control={<Button variant="outline">Alterar senha</Button>} />
                  <div style={{ height: 1, background: 'var(--border)' }} />
                  <Row title="Sessão atual" desc="Chrome · macOS · São Paulo" control={<Badge variant="secondary">este dispositivo</Badge>} />
                </CardContent>
              </Card>
            </>
          )}

          {cat === 'Idioma & região' && (
            <Card>
              <CardHeader><CardTitle>Idioma & região</CardTitle><CardDescription>Formato de datas e idioma da interface.</CardDescription></CardHeader>
              <CardContent style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <Field label="Idioma"><Select options={['Português (Brasil)', 'English']} defaultValue="Português (Brasil)" /></Field>
                <Field label="Fuso horário"><Select options={['America/Sao_Paulo (GMT-3)', 'UTC', 'Europe/Lisbon']} defaultValue="America/Sao_Paulo (GMT-3)" /></Field>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
window.ConfiguracoesScreen = ConfiguracoesScreen;
