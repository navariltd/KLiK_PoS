import type { SVGProps } from "react";

/**
 * QZ (thermal) print brand icon — a stylised "Q" + "Z".
 *
 * Inlined from cecypo_qz_extension's desk sprite (public/icons.svg,
 * symbol #icon-qz-print) so the klik_pos SPA renders the same mark the
 * extension shows next to the desk print buttons, without depending on that
 * app's sprite being loaded on the SPA page. Uses `currentColor`, so it
 * inherits text colour exactly like the lucide icons it sits beside.
 */
interface QzPrintIconProps extends Omit<SVGProps<SVGSVGElement>, "width" | "height"> {
  size?: number;
}

export default function QzPrintIcon({ size = 20, ...props }: QzPrintIconProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 16 16"
      aria-hidden="true"
      {...props}
    >
      {/* Q: circle + tail */}
      <circle cx="4" cy="7" r="3.2" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <line x1="6.2" y1="9.3" x2="8" y2="11.1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      {/* Z: three bars */}
      <polyline
        points="9.5,3.5 14.5,3.5 9.5,12.5 14.5,12.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
