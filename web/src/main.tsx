import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Link, Outlet, Route, Routes } from "react-router";
import "./base.css";
import AdminConsole from "./boards/admin/AdminConsole";
import { LoginPage, Portal, useMe } from "./boards/auth/AuthPages";
import BombBoard from "./boards/bomb/BombBoard";
import HelpBoard from "./boards/help/HelpBoard";
import MergeBoard from "./boards/merge/MergeBoard";
import ReviewBoard from "./boards/review/ReviewBoard";
import WishBoard from "./boards/wish/WishBoard";
import AppNav from "./components/AppNav";

// AI 聊天带 Markdown 渲染，体积大，按需加载
const ChatPage = lazy(() => import("./boards/chat/ChatPage"));
const Chat = () => <Suspense fallback={null}><ChatPage /></Suspense>;

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

// 加页面时：这里加一条路由，并把路径加进 backend/app.py 的 SPA_ROUTES、console.sites_view 的子站表。
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<Shell />}>
            <Route path="/" element={<Home />} />
            <Route path="/index.html" element={<Home />} />
            <Route path="/wish/*" element={<WishBoard />} />
            <Route path="/bomb/*" element={<BombBoard />} />
            <Route path="/review/*" element={<ReviewBoard />} />
            <Route path="/merge/*" element={<MergeBoard />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/chat/:id" element={<Chat />} />
            <Route path="/help/*" element={<HelpBoard />} />
            <Route path="*" element={<p style={{ padding: 24 }}>没有这个页面。<Link to="/">回大豆</Link></p>} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
