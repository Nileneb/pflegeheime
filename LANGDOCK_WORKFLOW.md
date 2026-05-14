# Langdock Workflow: Pflegeheim Data Cleaner

Ziel: **inhaltsgleicher Workflow** zu `data_cleaner.py` Ollama-Pfad — pro Eingabezeile saubere Kontaktdaten als JSON zurückgeben.

So, dass am Ende beide Pipelines (Ollama lokal vs. Langdock-Cloud) mit derselben Eingabe gefüttert + dieselbe DB-Spalte aktualisieren — fairer A/B-Vergleich für 50 Zeilen.

---

## 1. Workflow-Aufbau in Langdock

Erstelle in Langdock unter **Workflows → New Workflow**: **`pflegeheim-cleaner`**

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│ INPUT        │───▶│ Web Search   │───▶│ LLM Node     │───▶│ OUTPUT       │
│ (variables)  │    │ (DuckDuckGo  │    │ (JSON mode)  │    │ (JSON Schema)│
└──────────────┘    │  oder Tavily)│    └──────────────┘    └──────────────┘
                    └──────────────┘
```

### Node 1 — INPUT (Variables)

| Variable | Typ | Beispielwert |
|---|---|---|
| `name` | string | `Seniorenzentrum am Haarbach` |
| `ort` | string | `Aachen` |
| `kreis` | string | `Städteregion Aachen` |
| `website` | string (optional) | `https://www.residenzen.de/...` |
| `scraped_telefon` | string (optional) | `+49 671 4831306-0` |
| `scraped_email` | string (optional) | `info@proagemedia.de` |
| `scraped_adresse` | string (optional) | (leer) |

### Node 2 — Web Search

- **Provider:** DuckDuckGo (Langdock-built-in) **oder** Tavily (besser, kostet aber)
- **Query-Template:**
  ```
  {{name}} {{ort}} Impressum Kontakt Telefon Email
  ```
- **Region:** `de-de`
- **Max Results:** `6`
- **Output-Variable:** `search_results` (Array von `{title, url, snippet}`)

### Node 3 — LLM (JSON Mode)

- **Modell-Empfehlung:** `claude-haiku-4-5` (schnell+billig, Anthropic) oder `gpt-4o-mini` (OpenAI)
  Nicht `gpt-3.5` — halluziniert Telefonnummern.
- **Temperature:** `0.1`
- **JSON Mode:** ✅ aktiviert (Strict JSON / Structured Output)
- **System Prompt:** siehe unten Block A (1:1 aus `data_cleaner.py`)
- **User Prompt:** siehe unten Block B
- **Output Variable:** `cleaned_json`

### Node 4 — OUTPUT

- **Type:** JSON
- **Schema:** siehe unten Block C
- **Mapping:** `cleaned_json` 1:1 durchreichen

---

## 2. Exakte Prompts (1:1 übernehmen)

### Block A — System Prompt

```
Du bist ein Data-Cleaner-Agent für Pflegeheime in NRW.
Du erhältst bereits gescrapte Daten und frische Suchergebnisse aus dem Web.
Extrahiere die korrekten, verifizierten Kontaktdaten für genau diese Einrichtung.

Antworte AUSSCHLIESSLICH als valides JSON-Objekt mit diesen Feldern:
{
  "telefon": "...",
  "email": "...",
  "adresse": "...",
  "geschaeftsfuehrung": "...",
  "einrichtungsleitung": "...",
  "notes": "..."
}

Regeln:
- Nur Daten aus den vorliegenden Quellen — KEINE HALLUZINATIONEN
- Wenn keine verlässlichen Daten vorhanden: "No clear Data"
- Telefon im Format: 0XXX XXXXXXX oder +49 XXX XXXXXXX
- Email vollständig (user@domain.de)
- Adresse: Straße Nr, PLZ Ort
- notes: kurze Anmerkung falls Datenqualität unklar
```

### Block B — User Prompt (Template, mit Langdock-`{{var}}`-Syntax)

```
Einrichtung: {{name}}, {{ort}} ({{kreis}})

=== Scraped data ===
website: {{website}}
telefon: {{scraped_telefon}}
email: {{scraped_email}}
adresse: {{scraped_adresse}}

=== Web search: {{name}} {{ort}} Impressum Kontakt Telefon Email ===
{{#each search_results}}
[{{title}}] {{url}}
{{snippet}}

{{/each}}
```

### Block C — Output JSON Schema (für Strict-Mode)

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["telefon", "email", "adresse", "geschaeftsfuehrung", "einrichtungsleitung", "notes"],
  "properties": {
    "telefon":             { "type": "string" },
    "email":               { "type": "string" },
    "adresse":             { "type": "string" },
    "geschaeftsfuehrung":  { "type": "string" },
    "einrichtungsleitung": { "type": "string" },
    "notes":               { "type": "string" }
  }
}
```

---

## 3. Workflow per HTTP-API aufrufen (so mappt es auf data_cleaner.py)

Sobald der Workflow in Langdock veröffentlicht ist, gibt's eine **Run-URL** wie:

```
POST https://api.langdock.com/v1/workflows/{workflow_id}/run
Authorization: Bearer {LANGDOCK_API_KEY}
Content-Type: application/json
```

**Body:**
```json
{
  "inputs": {
    "name": "Seniorenzentrum am Haarbach",
    "ort": "Aachen",
    "kreis": "Städteregion Aachen",
    "website": "https://www.residenzen.de/...",
    "scraped_telefon": "+49 671 4831306-0",
    "scraped_email": "info@proagemedia.de",
    "scraped_adresse": ""
  }
}
```

**Response (gewünscht):**
```json
{
  "outputs": {
    "telefon": "0241 1234567",
    "email": "info@haarbach.de",
    "adresse": "Haarbachstr. 12, 52066 Aachen",
    "geschaeftsfuehrung": "Max Mustermann",
    "einrichtungsleitung": "Erika Beispiel",
    "notes": "Daten aus offizieller Website verifiziert"
  }
}
```

---

## 4. Wenn der Workflow steht → so im data_cleaner.py einbinden

Sobald du `LANGDOCK_API_KEY` und `LANGDOCK_WORKFLOW_ID` hast, sag mir Bescheid — dann ersetze ich `ollama_clean()` per `--cleaner langdock` Flag durch einen `langdock_clean()`-Aufruf, der genau diesen HTTP-Endpoint trifft. So wird derselbe Run mit `--cleaner-tag langdock` ausgeführt, in dieselbe Tabelle geschrieben, und das xlsx-Summary-Sheet zeigt **OK / Suspect / Empty** je Cleaner gegenübergestellt.

Das ist der A/B-Beweis für deinen Chef.

---

## 5. Fairness-Hinweise für den 50-Zeilen-Vergleich

- **Gleiche Eingabe-Zeilen:** Markier in Postgres die gleichen 50 `api_id`s — z. B. `cleaned = FALSE` reset und beide Cleaner abwechselnd. Oder: Ollama macht erst die 50, danach `UPDATE pflegeheime SET cleaned=FALSE WHERE cleaner='ollama'` und Langdock kriegt dieselbe Welle.
- **Gleiche Web-Search:** DuckDuckGo bei beiden — sonst vergleichst du LLM-Qualität + Suche durcheinander.
- **Gleicher System-Prompt:** ist identisch (Block A).
- **Unterschied liegt nur am LLM** (Qwen3.5:9b lokal vs. Haiku/GPT-4o-mini Cloud).

So weißt du am Ende: spart Langdock dir Zeit und liefert bessere Quote — oder reicht Ollama völlig?
