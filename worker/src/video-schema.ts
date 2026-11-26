/** Interpret the keywords used by the shared Draft 2020-12 schema.
 * No dynamic code generation; safe in the Cloudflare Worker runtime.
 */
import schema from "../../schemas/video.schema.json";

type Rule = {
  $ref?: string;
  type?: string;
  enum?: readonly unknown[];
  const?: unknown;
  properties?: Record<string, Rule>;
  patternProperties?: Record<string, Rule>;
  additionalProperties?: boolean;
  propertyNames?: Rule;
  maxProperties?: number;
  required?: readonly string[];
  items?: Rule;
  minItems?: number;
  maxItems?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  allOf?: readonly Rule[];
  if?: Rule;
  then?: Rule;
  not?: Rule;
};
export type ScriptIssue = {path: string; reason: string};
const root = schema as Rule & {$defs: Record<string, Rule>};
const own = (value: object, key: string) => Object.prototype.hasOwnProperty.call(value, key);
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
// Avoid echoing arbitrary user-supplied keys (which could contain secrets or control characters).
const child = (path: string, key: string) => {
  const safe = /^[a-zA-Z_][a-zA-Z0-9_]{0,39}$/.test(key) ? key : "[campo]";
  return safe === "[campo]" ? path + safe : path ? path + "." + safe : safe;
};

function validate(value: unknown, rule: Rule, path: string): ScriptIssue | undefined {
  const issue = (reason: string, at = path): ScriptIssue => ({path: at || "script", reason});
  if (rule.$ref) {
    const name = rule.$ref.replace(/^#\/\$defs\//, "");
    if (!own(root.$defs, name)) throw new Error("Unsupported schema reference");
    return validate(value, root.$defs[name]!, path);
  }
  if (rule.type) {
    const valid = rule.type === "object" ? object(value) :
      rule.type === "array" ? Array.isArray(value) :
      rule.type === "integer" ? Number.isInteger(value) :
      rule.type === "number" ? typeof value === "number" && Number.isFinite(value) :
      rule.type === "string" ? typeof value === "string" : false;
    if (!valid) return issue("tipo esperado: " + rule.type);
  }
  if (rule.enum && !rule.enum.includes(value)) return issue("valor fuera de las opciones permitidas");
  if (own(rule, "const") && value !== rule.const) return issue("valor no permitido");
  if (typeof value === "string") {
    const length = [...value].length;
    if (rule.minLength !== undefined && length < rule.minLength) return issue("longitud minima: " + rule.minLength);
    if (rule.maxLength !== undefined && length > rule.maxLength) return issue("longitud maxima: " + rule.maxLength);
    if (rule.pattern !== undefined && !new RegExp(rule.pattern, "u").test(value)) return issue("formato no permitido");
  }
  if (typeof value === "number") {
    if (rule.minimum !== undefined && value < rule.minimum) return issue("minimo: " + rule.minimum);
    if (rule.maximum !== undefined && value > rule.maximum) return issue("maximo: " + rule.maximum);
    if (rule.exclusiveMinimum !== undefined && value <= rule.exclusiveMinimum) return issue("debe superar " + rule.exclusiveMinimum);
  }
  if (Array.isArray(value)) {
    if (rule.minItems !== undefined && value.length < rule.minItems) return issue("minimo de elementos: " + rule.minItems);
    if (rule.maxItems !== undefined && value.length > rule.maxItems) return issue("maximo de elementos: " + rule.maxItems);
    if (rule.items) for (let i = 0; i < value.length; i++) {
      const error = validate(value[i], rule.items, path + "[" + i + "]");
      if (error) return error;
    }
  }
  if (object(value)) {
    const keys = Object.keys(value);
    if (rule.maxProperties !== undefined && keys.length > rule.maxProperties) return issue("maximo de campos: " + rule.maxProperties);
    for (const key of rule.required ?? []) if (!own(value, key)) return issue("campo obligatorio", child(path, key));
    for (const key of keys) {
      const at = child(path, key);
      if (rule.propertyNames) {
        const error = validate(key, rule.propertyNames, at);
        if (error) return error;
      }
      let matched = false;
      if (rule.properties && own(rule.properties, key)) {
        matched = true;
        const error = validate(value[key], rule.properties[key]!, at);
        if (error) return error;
      }
      for (const [pattern, propertyRule] of Object.entries(rule.patternProperties ?? {})) {
        if (!new RegExp(pattern, "u").test(key)) continue;
        matched = true;
        const error = validate(value[key], propertyRule, at);
        if (error) return error;
      }
      if (!matched && rule.additionalProperties === false) return issue("campo no permitido", at);
    }
  }
  if (rule.not && !validate(value, rule.not, path)) return issue("condicion no permitida");
  if (rule.if && !validate(value, rule.if, path) && rule.then) {
    const error = validate(value, rule.then, path);
    if (error) return error;
  }
  for (const sub of rule.allOf ?? []) {
    const error = validate(value, sub, path);
    if (error) return error;
  }
  return undefined;
}

export function validateVideoScript(value: unknown): ScriptIssue[] {
  const error = validate(value, root, "");
  return error ? [error] : [];
}
