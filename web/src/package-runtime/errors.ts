export class RuntimeError extends Error {
  constructor(
    message: string,
    readonly code = "runtime_error",
    readonly operation = "",
  ) {
    super(message);
    this.name = "RuntimeError";
  }
  toJSON() {
    return {
      message: this.message,
      code: this.code,
      operation: this.operation,
    };
  }
  static from(value: unknown): RuntimeError {
    if (typeof value === "object" && value !== null && "message" in value) {
      const e = value as {
        message: unknown;
        code?: unknown;
        operation?: unknown;
      };
      return new RuntimeError(
        String(e.message),
        typeof e.code === "string" ? e.code : undefined,
        typeof e.operation === "string" ? e.operation : undefined,
      );
    }
    return new RuntimeError(String(value));
  }
}
