// utils/batch.ts
interface BatchFetchBody {
  message?: unknown;
  exception?: string;
}

export async function getBatches(itemCode: string) {
  const response = await fetch(
    `/api/method/klik_pos.api.item.item_details.get_batch_nos_with_qty?item_code=${encodeURIComponent(itemCode)}`
  );

  const resData: BatchFetchBody | null = await response.json().catch(() => null);

  if (!response.ok) {
    const serverMessage =
      (typeof resData?.exception === "string" && resData.exception) ||
      (typeof resData?.message === "string" && resData.message) ||
      undefined;

    throw new Error(
      serverMessage
        ? `Failed to fetch batches (HTTP ${response.status}): ${serverMessage}`
        : `Failed to fetch batches: request failed with status ${response.status}`
    );
  }

  if (resData?.message && Array.isArray(resData.message)) {
    return resData.message;
  }

  throw new Error("Invalid response format");
}
