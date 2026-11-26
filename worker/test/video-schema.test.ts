import {describe, expect, it} from "vitest";
import {validateVideoScript} from "../src/video-schema";
import cases from "../../tests/fixtures/video-schema-parity.json";
import chile from "../../docs/ejemplos/chile-vertical.json";
import minimal from "../../docs/ejemplos/guion-minimo.json";
import planta from "../../docs/ejemplos/planta-cuadrado.json";
import solar from "../../docs/ejemplos/sistema-solar-horizontal.json";

describe("shared video schema parity with Python Draft202012Validator",()=>{
  it.each(cases)("$name",({value,valid})=>{
    expect(validateVideoScript(value).length===0).toBe(valid);
  });
  it.each([chile,minimal,planta,solar])("accepts the documented example %#",value=>{
    expect(validateVideoScript(value)).toEqual([]);
  });
});
