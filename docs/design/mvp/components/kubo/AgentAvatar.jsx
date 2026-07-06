
import { Icon } from '../icons/Icon.jsx';

const SIZES = { sm: { box: 28, icon: 14 }, md: { box: 32, icon: 16 }, lg: { box: 40, icon: 20 } };

export function AgentAvatar({ avatar, name, size = 'md', style }) {
  const s = SIZES[size] || SIZES.md;
  return (
    <div title={name} style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
      width: s.box, height: s.box, borderRadius: 9999, background: 'var(--muted)',
      lineHeight: 1, ...style,
    }}>
      {avatar ? (
        <img src={avatar} alt={name || 'Agent'} style={{ width: s.box, height: s.box, borderRadius: 9999, objectFit: 'cover' }} />
      ) : (
        <Icon name="bot" size={s.icon} style={{ color: 'var(--muted-foreground)' }} />
      )}
    </div>
  );
}
