import { Component, lazy, type ReactNode, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Link, Outlet, Route, Routes } from "react-router";
import "./base.css";
import AdminConsole from "./boards/admin/AdminConsole";
import { LoginPage, Portal, useMe, useSites } from "./boards/auth/AuthPages";
import BombBoard from "./boards/bomb/BombBoard";
import DocWatchBoard from "./boards/docwatch/DocWatchBoard";
import HelpBoard from "./boards/help/HelpBoard";
import MergeBoard from "./boards/merge/MergeBoard";
import MetaBoard from "./boards/meta/MetaBoard";
import ReviewBoard from "./boards/review/ReviewBoard";
import WishBoard from "./boards/wish/WishBoard";
import AppNav from "./components/AppNav";

// 重新构建后，开着的旧页面里记的分块文件名（ChatPage-xxx.js）已被删掉，按需加载会失败。
// 这时自动刷新一次拿新版本；10 秒内刷过还失败就不再刷，交给 LazyBoundary 显示提示，免得来回刷。
window.addEventListener("vite:preloadError", (event) => {
  try {
    const last = Number(sessionStorage.getItem("chunk-reload-at") || 0);
    if (Date.now() - last < 10_000) return;
    sessionStorage.setItem("chunk-reload-at", String(Date.now()));
  } catch {
    return;
  }
  event.preventDefault();
  location.reload();
});

/** 按需加载的页面出错时只在内容区显示提示，不让整个页面（连导航条）白屏。 */
class LazyBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return <p style={{ padding: 24 }}>页面没加载出来（可能刚更新过）。<a href={location.href}>刷新一下</a></p>;
  }
}

// AI 聊天带 Markdown 渲染，体积大，按需加载
const ChatPage = lazy(() => import("./boards/chat/ChatPage"));
const Chat = () => (
  <LazyBoundary>
    <Suspense fallback={<p style={{ padding: 24, color: "#888" }}>正在加载…</p>}><ChatPage /></Suspense>
  </LazyBoundary>
);

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } } });

/** 首页：管理员看后台，普通用户看子站入口。 */
function Home() {
  const { data: me, error } = useMe();
  if (error) return <p style={{ padding: 24 }}>{error.message}</p>;
  if (!me) return null;
  return me.role === "admin" ? <AdminConsole me={me} /> : <Portal me={me} />;
}

/** 登录后的页面都带顶部导航条，看板之间站内切换。 */
function Shell() {
  return (
    <>
      <AppNav />
      <Outlet />
    </>
  );
}

/** 管理员关掉的看板：普通用户打开时只显示「已关闭」（接口那边也会 403）。 */
function Gate({ port, children }: { port: string; children: ReactNode }) {
  const closed = useSites().data?.closed ?? [];
  if (closed.includes(port)) return <p style={{ padding: 24 }}>这个看板管理员暂时关闭了。<Link to="/">回大豆</Link></p>;
  return <>{children}</>;
}

// 加页面时：这里加一条路由，并把路径加进 backend/app.py 的 SPA_ROUTES、console.sites_view 的子站表、boards/board_access.py 的 API_PREFIXES。
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<Shell />}>
            <Route path="/" element={<Home />} />
            <Route path="/index.html" element={<Home />} />
            <Route path="/wish/*" element={<Gate port="/wish"><WishBoard /></Gate>} />
            <Route path="/bomb/*" element={<Gate port="/bomb"><BombBoard /></Gate>} />
            <Route path="/review/*" element={<Gate port="/review"><ReviewBoard /></Gate>} />
            <Route path="/merge/*" element={<Gate port="/merge"><MergeBoard /></Gate>} />
            <Route path="/chat" element={<Gate port="/chat"><Chat /></Gate>} />
            <Route path="/chat/:id" element={<Gate port="/chat"><Chat /></Gate>} />
            <Route path="/docwatch/*" element={<Gate port="/docwatch"><DocWatchBoard /></Gate>} />
            <Route path="/help/*" element={<Gate port="/help"><HelpBoard /></Gate>} />
            <Route path="/meta/*" element={<Gate port="/meta"><MetaBoard /></Gate>} />
            <Route path="*" element={<p style={{ padding: 24 }}>没有这个页面。<Link to="/">回大豆</Link></p>} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
