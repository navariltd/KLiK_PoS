export class APIResponseError extends Error {
  readonly status: number;
  readonly url: string;
  readonly contentType: string;
  readonly responsePreview: string;
  readonly isAuthenticationError: boolean;

  constructor(
    message: string,
    details: {
      status: number;
      url: string;
      contentType: string;
      responsePreview: string;
      isAuthenticationError: boolean;
    },
  ) {
    super(message);
    this.name = "APIResponseError";
    this.status = details.status;
    this.url = details.url;
    this.contentType = details.contentType;
    this.responsePreview = details.responsePreview;
    this.isAuthenticationError = details.isAuthenticationError;
  }
}

function getResponsePreview(body: string): string {
  return body
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<style[\s\S]*?<\/style>/gi, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 240);
}

export async function parseAPIResponse<T = unknown>(response: Response): Promise<T> {
  const body = await response.text();
  const contentType = response.headers.get("content-type") || "";

  try {
    return response.json()
  } catch {
    const responsePreview = getResponsePreview(body);
    const isHtml = /<!doctype\s+html|<html[\s>]/i.test(body);
    const isAuthenticationError =
      response.status === 401 ||
      response.status === 403 ||
      /\/login(?:[/?#]|$)/i.test(response.url) ||
      /session expired|please login|log in to continue/i.test(responsePreview);

    const message = isAuthenticationError
      ? "Your session has expired. Please log in again."
      : isHtml
        ? `The server returned an HTML error page instead of JSON (HTTP ${response.status}).`
        : `The server returned an invalid JSON response (HTTP ${response.status}).`;

    console.error("Invalid API response", {
      status: response.status,
      statusText: response.statusText,
      url: response.url,
      contentType,
      responsePreview,
    });

    throw new APIResponseError(message, {
      status: response.status,
      url: response.url,
      contentType,
      responsePreview,
      isAuthenticationError,
    });
  }
}
