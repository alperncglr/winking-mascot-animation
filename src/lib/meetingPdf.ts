import type { Content, TDocumentDefinitions } from "pdfmake/interfaces";

export interface MeetingPdfMetadata {
  meetingName: string;
  date: string;
  startTime: string;
  endTime: string;
  duration: string;
  timeZone: string;
}

export interface DownloadMeetingPdfOptions {
  fileName: string;
  documentTitle: string;
  sectionTitle: string;
  metadata: MeetingPdfMetadata;
  body: string;
  kind: "transcript" | "summary";
}

const BRAND = "#0f766e";
const INK = "#172554";
const MUTED = "#64748b";
const LINE = "#dbe7e5";
const PAPER = "#f5faf9";

function cleanInlineMarkdown(value: string) {
  return value
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/__(.*?)__/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .trim();
}

function transcriptContent(body: string): Content[] {
  const source = body.replace(/^#\s*Final Transcript\s*/i, "").trim();
  if (!source) return [{ text: "Konuşma dökümü bulunamadı.", color: MUTED, italics: true }];

  const rows: Content[] = [];
  for (const line of source.split(/\r?\n/).filter(Boolean)) {
    const match = line.match(/^(\d{2}:\d{2}:\d{2})\s+-\s+(.+?)\s+-\s+(.+)$/);
    if (!match) {
      rows.push({ text: cleanInlineMarkdown(line), margin: [0, 0, 0, 8] });
      continue;
    }
    rows.push({
      stack: [
        {
          columns: [
            { text: match[1] ?? "", width: 58, color: BRAND, bold: true, fontSize: 9 },
            { text: match[2] ?? "", color: INK, bold: true, fontSize: 9 },
          ],
          margin: [0, 0, 0, 3],
        },
        { text: match[3] ?? "", color: "#25324a", lineHeight: 1.28 },
      ],
      margin: [0, 0, 0, 12],
    } as Content);
  }
  return rows;
}

function summaryContent(body: string): Content[] {
  const rows: Content[] = [];
  for (const rawLine of body.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) {
      rows.push({ text: "", margin: [0, 0, 0, 4] });
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      rows.push({
        text: cleanInlineMarkdown(heading[2] ?? ""),
        color: (heading[1]?.length ?? 0) === 1 ? INK : BRAND,
        bold: true,
        fontSize: (heading[1]?.length ?? 0) === 1 ? 15 : 12,
        margin: [0, 8, 0, 6],
      });
      continue;
    }
    const nestedBullet = rawLine.match(/^\s{2,}[-*]\s+(.+)$/);
    if (nestedBullet) {
      rows.push({
        ul: [cleanInlineMarkdown(nestedBullet[1] ?? "")],
        margin: [24, 0, 0, 4],
        color: MUTED,
      });
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      rows.push({ ul: [cleanInlineMarkdown(bullet[1] ?? "")], margin: [8, 0, 0, 5] });
      continue;
    }
    const numbered = line.match(/^\d+[.)]\s+(.+)$/);
    if (numbered) {
      rows.push({ ol: [cleanInlineMarkdown(numbered[1] ?? "")], margin: [8, 0, 0, 5] });
      continue;
    }
    rows.push({ text: cleanInlineMarkdown(line), lineHeight: 1.28, margin: [0, 0, 0, 7] });
  }
  return rows.length ? rows : [{ text: "Toplantı özeti bulunamadı.", color: MUTED, italics: true }];
}

export function buildMeetingPdfDefinition(options: DownloadMeetingPdfOptions): TDocumentDefinitions {
  const metadataRows: Array<[string, string]> = [
    ["Toplantı adı", options.metadata.meetingName],
    ["Tarih", options.metadata.date],
    ["Başlangıç", options.metadata.startTime],
    ["Bitiş", options.metadata.endTime],
    ["Süre", options.metadata.duration],
    ["Saat dilimi", options.metadata.timeZone],
  ];

  return {
    pageSize: "A4",
    pageMargins: [46, 52, 46, 50],
    defaultStyle: { font: "Roboto", fontSize: 10.5, color: "#25324a" },
    info: { title: `${options.metadata.meetingName} - ${options.documentTitle}`, creator: "T3AI DEFTER" },
    footer: (currentPage, pageCount) => ({
      columns: [
        { text: "T3AI DEFTER · T3AI tarafından geliştirildi", color: MUTED, fontSize: 8 },
        { text: `${currentPage} / ${pageCount}`, alignment: "right", color: MUTED, fontSize: 8 },
      ],
      margin: [46, 12, 46, 0],
    }),
    content: [
      { text: "T3AI DEFTER", color: BRAND, bold: true, characterSpacing: 1.2, fontSize: 10 },
      { text: options.documentTitle, color: INK, bold: true, fontSize: 24, margin: [0, 5, 0, 18] },
      {
        table: {
          widths: [92, "*"],
          body: metadataRows.map(([label, value]) => [
            { text: label, bold: true, color: BRAND, margin: [5, 4, 5, 4] },
            { text: value, color: INK, margin: [5, 4, 5, 4] },
          ]),
        },
        layout: {
          fillColor: (rowIndex) => (rowIndex % 2 === 0 ? PAPER : "#ffffff"),
          hLineColor: () => LINE,
          vLineColor: () => LINE,
          hLineWidth: () => 0.6,
          vLineWidth: () => 0.6,
        },
        margin: [0, 0, 0, 22],
      },
      ...(options.sectionTitle
        ? [
            {
              text: options.sectionTitle,
              color: BRAND,
              bold: true,
              fontSize: 14,
              margin: [0, 0, 0, 12],
            } as Content,
          ]
        : []),
      ...(options.kind === "transcript" ? transcriptContent(options.body) : summaryContent(options.body)),
    ],
  };
}

export async function downloadMeetingPdf(options: DownloadMeetingPdfOptions) {
  const [pdfMake, fontsModule] = await Promise.all([
    import("pdfmake/build/pdfmake"),
    import("pdfmake/build/vfs_fonts"),
  ]);
  const virtualFonts = "default" in fontsModule ? fontsModule.default : fontsModule;
  pdfMake.addVirtualFileSystem(virtualFonts);

  await pdfMake.createPdf(buildMeetingPdfDefinition(options)).download(options.fileName);
}
