import { NavLink, Routes, Route } from "react-router-dom";
import Chat from "./pages/Chat";
import Approvals from "./pages/Approvals";
import Ooda from "./pages/Ooda";
import Observe from "./pages/Observe";
import Config from "./pages/Config";

export default function App() {
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          太一
          <small>Taiyi · Agent OS</small>
        </div>
        <NavLink to="/" end className={({ isActive }) => "navlink" + (isActive ? " active" : "")}>
          对话
        </NavLink>
        <NavLink to="/approvals" className={({ isActive }) => "navlink" + (isActive ? " active" : "")}>
          人工审批
        </NavLink>
        <NavLink to="/ooda" className={({ isActive }) => "navlink" + (isActive ? " active" : "")}>
          OODA 审查
        </NavLink>
        <NavLink to="/observe" className={({ isActive }) => "navlink" + (isActive ? " active" : "")}>
          记忆 / 指标
        </NavLink>
        <NavLink to="/config" className={({ isActive }) => "navlink" + (isActive ? " active" : "")}>
          配置
        </NavLink>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Chat />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/ooda" element={<Ooda />} />
          <Route path="/observe" element={<Observe />} />
          <Route path="/config" element={<Config />} />
        </Routes>
      </main>
    </div>
  );
}
