/**
 * 컴파일 산출물에서 ABI 만 뽑아 blockchain/abi/ 에 남긴다.
 *
 * artifacts/ 는 .gitignore 에 있어 저장소에 남지 않는다. 그런데 백엔드·프론트·워커가
 * 호출부를 만들려면 ABI 가 필요하고, 매번 손으로 복사하면 반드시 코드와 어긋난다.
 * 그래서 컴파일 결과에서 기계적으로 뽑아 커밋한다.
 *
 * 상태 enum 은 ABI 에 uint8 로만 나오고 이름이 사라진다. 연동 측이 숫자를 해석할 수
 * 있도록 소스에서 읽어 함께 남긴다.
 *
 *   npm run abi
 */
import { execSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

const CONTRACTS = [
  {
    name: "GPUJobEscrow",
    artifact: "artifacts/contracts/escrow/GPUJobEscrow.sol/GPUJobEscrow.json",
    source: "contracts/escrow/GPUJobEscrow.sol",
  },
];

/** 소스에서 enum 정의를 읽어 이름과 번호를 짝지어 둔다. */
function readStateEnum(sourcePath) {
  const src = readFileSync(join(root, sourcePath), "utf8");
  const match = src.match(/enum\s+JobState\s*\{([^}]+)\}/);
  if (!match) return null;

  const states = {};
  match[1]
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .forEach((name, index) => {
      states[index] = name;
    });
  return states;
}

function shortCommit() {
  try {
    return execSync("git rev-parse --short HEAD", { cwd: root }).toString().trim();
  } catch {
    return "unknown";
  }
}

mkdirSync(join(root, "abi"), { recursive: true });

for (const { name, artifact, source } of CONTRACTS) {
  const built = JSON.parse(readFileSync(join(root, artifact), "utf8"));

  const out = {
    contractName: name,
    sourceCommit: shortCommit(),
    solcVersion: "0.8.28",
    evmVersion: "paris",
    generatedAt: new Date().toISOString(),
    deployed: false,
    note: "아직 배포하지 않았습니다. 배포 후 주소는 deployments/ 에 따로 기록합니다.",
    stateEnum: readStateEnum(source),
    abi: built.abi,
  };

  const target = join(root, "abi", `${name}.json`);
  writeFileSync(target, `${JSON.stringify(out, null, 2)}\n`, "utf8");

  const fns = out.abi.filter((e) => e.type === "function").length;
  const events = out.abi.filter((e) => e.type === "event").length;
  const errors = out.abi.filter((e) => e.type === "error").length;
  console.log(`${name}  함수 ${fns} · 이벤트 ${events} · 오류 ${errors}  → abi/${name}.json`);
}
