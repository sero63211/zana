import { describe, expect, it } from "vitest";

import { basename } from "./format";

describe("format helpers", () => {
  it("extracts basenames from POSIX and Windows paths", () => {
    expect(basename("/Users/sero/ZANA/zana.sqlite3")).toBe("zana.sqlite3");
    expect(basename("C:\\Users\\sero\\ZANA\\zana.sqlite3")).toBe("zana.sqlite3");
    expect(basename("artifacts")).toBe("artifacts");
  });
});
