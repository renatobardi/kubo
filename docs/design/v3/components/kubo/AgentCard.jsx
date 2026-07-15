import { AgentAvatar } from './AgentAvatar.jsx';
import { Icon } from '../icons/Icon.jsx';

export function AgentCard({ agent = {}, onClick, style }) {
  return (
    <button type="button" onClick={onClick}
      style={{
        display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 12,
        width: '100%', padding: 16, textAlign: 'left', cursor: 'pointer',
        background: 'var(--card)', border: '1px solid var(--border)',
        borderRadius: 'var(--radius-xl)', boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
        transition: 'all 150ms',
        ...style,
      }}
      className="kubo-agent-card"
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'color-mix(in oklab, var(--primary) 30%, transparent)'; e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.08)'; }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.boxShadow = '0 1px 2px rgba(0,0,0,0.04)'; }}
      onMouseDown={(e) => e.currentTarget.style.transform = 'scale(0.99)'}
      onMouseUp={(e) => e.currentTarget.style.transform = ''}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, width: '100%' }}>
        <AgentAvatar avatar={agent.avatar} name={agent.name} size="lg" />
        <div style={{ minWidth: 0, flex: 1 }}>
          <p style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--foreground)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{agent.name}</p>
          {agent.description ? (
            <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--muted-foreground)', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{agent.description}</p>
          ) : (
            <p style={{ margin: '2px 0 0', fontSize: 12, fontStyle: 'italic', color: 'color-mix(in oklab, var(--muted-foreground) 50%, transparent)' }}>No description</p>
          )}
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', fontSize: 12, color: 'var(--muted-foreground)' }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Icon name="message-square" size={12} /> Start chatting
        </span>
      </div>
    </button>
  );
}
