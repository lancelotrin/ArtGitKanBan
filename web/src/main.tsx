import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router";
import "./base.css";
import AdminConsole from "./boards/admin/AdminConsole";
import { LoginPage, Portal, useMe } from "./boards/auth/AuthPages";
import BombBoard from "./boards/bomb/BombBoard";
import MergeBoard from "./boards/merge/MergeBoard";
import ReviewBoard from "./boards/review/ReviewBoard";
import WishBoard from "./boards/wish/WishBoard";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } } });

/** 首页：管理员看后台，普通用户看子站入口。 */
function Home() {
  const { data: me, error } = useMe();
  if (error) return <p style={{ padding: 24 }}>{error.message}</p>;
  if (!me) return null;
  return me.role === "admin" ? <AdminConsole me={me} /> : <Portal me={me} />;
}

// 加页面时：这里加一条路由，并把路径加进 backend/app.py 的 SPA_ROUTES。
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/index.html" element={<Home />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/wish/*" element={<WishBoard />} />
          <Route path="/bomb/*" element={<BombBoard />} />
          <Route path="/review/*" element={<ReviewBoard />} />
          <Route path="/merge/*" element={<MergeBoard />} />
          <Route path="*" element={<p style={{ padding: 24 }}>没有这个页面。<a href="/">回大豆</a></p>} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
