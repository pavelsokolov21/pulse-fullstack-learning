"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";

import { auth } from "@/api/client";
import { useSession } from "@/lib/use-session";

export default function LoginPage() {
  const router = useRouter();
  const session = useSession();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Если cookie с refresh ещё жива, вход не нужен: сессия восстановится сама.
  useEffect(() => {
    void auth.restore();
  }, []);

  useEffect(() => {
    if (session.status === "authenticated") router.replace("/monitors");
  }, [session.status, router]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);

    const result = await auth.login(
      String(form.get("email")),
      String(form.get("password")),
    );
    setPending(false);

    if (result.ok) return; // редирект сделает эффект выше
    if (result.reason === "invalid_credentials") {
      setError("Неверный email или пароль");
    } else if (result.reason === "rate_limited") {
      setError(
        result.retryAfter
          ? `Слишком много попыток. Повторите через ${result.retryAfter} с`
          : "Слишком много попыток. Повторите позже",
      );
    } else {
      setError("Сервер недоступен. Попробуйте позже");
    }
  }

  return (
    <main>
      <h1>Вход в Pulse</h1>
      <form className="form" onSubmit={onSubmit}>
        <label className="field">
          Email
          <input name="email" type="email" autoComplete="username" required />
        </label>
        <label className="field">
          Пароль
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
          />
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={pending}>
          {pending ? "Входим…" : "Войти"}
        </button>
      </form>
    </main>
  );
}
