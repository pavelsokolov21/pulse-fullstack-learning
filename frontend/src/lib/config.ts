// Браузер ходит на тот же origin, что и страница: Next.js перенаправляет /api/*
// на FastAPI (см. rewrites в next.config.ts). Поэтому нет CORS, а cookie с
// SameSite=Strict работает без оговорок.
export const API_BASE_URL = "/api";
