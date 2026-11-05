import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router";
import "./base.css";
import WishBoard from "./boards/wish/WishBoard";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } } });

// 每迁一个看板，在这里加一条路由，并把前缀加进 server/app.py 的 SPA_PREFIXES。
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/wish/*" element={<WishBoard />} />
          <Route path="*" element={<p style={{ padding: 24 }}>没有这个看板。<a href="/">回大豆后台</a></p>} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
