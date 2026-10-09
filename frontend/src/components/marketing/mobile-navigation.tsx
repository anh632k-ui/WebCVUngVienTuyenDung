"use client";

import Link from "next/link";
import { useRef } from "react";
import { Icon } from "./icons";

type NavigationItem = { href: string; label: string };

export function MobileNavigation({ items }: { items: readonly NavigationItem[] }) {
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const closeMenu = () => detailsRef.current?.removeAttribute("open");

  return (
    <details className="mobile-navigation" ref={detailsRef}>
      <summary aria-label="Mở hoặc đóng menu điều hướng">
        <Icon name="menu" size={22} /><span className="sr-only">Menu</span>
      </summary>
      <nav aria-label="Điều hướng trên điện thoại">
        {items.map((item) => <Link key={item.href} href={item.href} onClick={closeMenu}>{item.label}</Link>)}
        <Link href="/dang-nhap" onClick={closeMenu}>Đăng nhập</Link>
        <Link className="mobile-nav-cta" href="/dang-ky" onClick={closeMenu}>Đăng ký</Link>
      </nav>
    </details>
  );
}
