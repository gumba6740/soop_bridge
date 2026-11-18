import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// 개발 서버(npm run dev) 설정.
//
// 브라우저는 /api/... 로만 요청한다. 개발 중에는 이 프록시가,
// 배포 후에는 nginx가 /api를 떼고 FastAPI로 넘긴다.
//   브라우저 → /api/station/ecvhao → (프록시) → http://localhost:8000/station/ecvhao
// 브라우저 입장에서는 프론트와 API가 같은 주소라서 CORS가 생기지 않는다.
//
// API 주소는 .env.local 파일의 API_TARGET으로 바꿀 수 있다.
// 예) 서버 데이터를 보고 싶으면  API_TARGET=http://<서버 IP>:8000
export default defineConfig(({ mode }) => {
    const env = loadEnv(mode, process.cwd(), "");
    const target = env.API_TARGET || "http://localhost:8000";

    return {
        plugins: [react(), tailwindcss()],
        server: {
            proxy: {
                "/api": {
                    target,
                    changeOrigin: true,
                    rewrite: (path) => path.replace(/^\/api/, ""),
                },
            },
        },
    };
});