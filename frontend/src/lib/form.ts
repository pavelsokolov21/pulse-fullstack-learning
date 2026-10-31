/**
 * Тело `application/x-www-form-urlencoded`: `a=1&b=x%20y`.
 *
 * openapi-fetch по умолчанию делает `JSON.stringify`, а вход по OAuth2 password
 * flow принимает форму. Значения `null` и `undefined` пропускаются, как и у
 * поля формы, которого нет. `URLSearchParams` сам экранирует спецсимволы.
 */
export function toFormBody(body: object): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(body)) {
    if (value !== null && value !== undefined) {
      params.append(key, String(value));
    }
  }
  return params.toString();
}

export const FORM_CONTENT_TYPE = "application/x-www-form-urlencoded";
