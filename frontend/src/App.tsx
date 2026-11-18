import { Route, Routes } from "react-router";
import Layout from "./components/Layout";
import { Message } from "./components/Status";
import HomePage from "./pages/HomePage";
import LivePage from "./pages/LivePage";
import StationPage from "./pages/StationPage";
import VodPage from "./pages/VodPage";

// 화면 주소 (브라우저 주소창에 보이는 것. API 주소와는 별개)
//   /                 스트리머 목록
//   /station/:bid     스트리머 상세 (VOD 목록)
//   /vod/:vodId       VOD 다시보기 (영상 + 채팅)
//   /live/:bid        라이브 (퍼가기 영상 + 실시간 채팅)
export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<HomePage />} />
        <Route path="station/:bid" element={<StationPage />} />
        <Route path="vod/:vodId" element={<VodPage />} />
        <Route path="live/:bid" element={<LivePage />} />
        <Route path="*" element={<Message title="없는 페이지예요" />} />
      </Route>
    </Routes>
  );
}
