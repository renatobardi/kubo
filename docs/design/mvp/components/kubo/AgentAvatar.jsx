import { Icon } from '../icons/Icon.jsx';

const SIZES = { sm: { box: 28, font: 14, icon: 14 }, md: { box: 32, font: 16, icon: 16 }, lg: { box: 40, font: 20, icon: 20 } };

function isEmoji(str) {
  if (!str) return false;
  const segments = [...str];
  return segments.length <= 2 && /\p{Emoji}/u.test(str);
}

export function AgentAvatar({ avatar, name, size = 'md', style }) {
  const s = SIZES[size] || SIZES.md;
  return (
    <div title={name} style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
      width: s.box, height: s.box, borderRadius: 9999, background: 'var(--muted)',
      fontSize: s.font, lineHeight: 1, ...style,
    }}>
      {isEmoji(avatar) ? (
        <span style={{ lineHeight: 1 }}>{avatar}</span>
      ) : avatar ? (
        <img src={avatar} alt={name || 'Agent'} style={{ width: s.box, height: s.box, borderRadius: 9999, objectFit: 'cover' }} />
      ) : (
        <Icon name="bot" size={s.icon} style={{ color: 'var(--muted-foreground)' }} />
      )}
    </div>
  );
}
