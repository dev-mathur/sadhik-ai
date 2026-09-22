import type { ComponentType } from 'react';
import type { IconProps } from '../lib/icons';

/** A decorative icon. The visible text next to it carries the meaning, so it is hidden from assistive technology. */
export function Glyph({ icon: Icon, size = 14 }: { icon: ComponentType<IconProps>; size?: number }) {
  return (
    <span className="glyph" aria-hidden="true">
      <Icon size={size} />
    </span>
  );
}
