interface SerialNumber {
  serial_no: string;
  [key: string]: unknown;
}

interface SerialFetchBody {
  message?: unknown;
  exception?: string;
}

export async function getSerials(itemCode: string): Promise<string[]> {
  const response = await fetch(`/api/method/klik_pos.api.item.item_details.get_serial_nos_for_item?item_code=${encodeURIComponent(itemCode)}`);

  const resData: SerialFetchBody | null = await response.json().catch(() => null);

  if (!response.ok) {
    const serverMessage =
      (typeof resData?.exception === "string" && resData.exception) ||
      (typeof resData?.message === "string" && resData.message) ||
      undefined;

    throw new Error(
      serverMessage
        ? `Failed to fetch serial numbers (HTTP ${response.status}): ${serverMessage}`
        : `Failed to fetch serial numbers: request failed with status ${response.status}`
    );
  }

  if (Array.isArray(resData?.message)) {
    return (resData.message as SerialNumber[])
      .map((s: SerialNumber) => typeof s.serial_no === 'string' ? s.serial_no : '')
      .filter(Boolean) as string[];
  }

  throw new Error("Invalid response format");
}
