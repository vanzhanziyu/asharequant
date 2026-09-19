import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = { title: 'A股行情监控面板', description: '市场情绪与指数 K 线监控' };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
