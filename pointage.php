<?php
// Endpoint API : Pointage quotidien par QR Code (Concours Génie UPB)
header("Access-Control-Allow-Origin: *");
header("Content-Type: application/json; charset=UTF-8");
header("Cache-Control: no-store");
header("Access-Control-Allow-Methods: POST, GET, OPTIONS");
header("Access-Control-Allow-Headers: Content-Type, Access-Control-Allow-Headers, Authorization, X-Requested-With");

// Pré-vol CORS
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(200);
    exit;
}

require_once 'admin_auth.php';
requireAdmin();
require_once 'db.php';

/**
 * Réponse JSON normalisée
 */
function respond(int $code, array $data): void {
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
    exit;
}

// GET ?date=YYYY-MM&jour=...  -> Rapport de présence par date
if ($_SERVER['REQUEST_METHOD'] === 'GET') {
    $date = $_GET['date'] ?? date('Y-m-d');
    $jour = $_GET['jour'] ?? null;

    // Valider le format de la date
    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $date)) {
        respond(400, ["status" => "error", "message" => "Date invalide. Format attendu : AAAA-MM-JJ."]);
    }

    try {
        $sql = "SELECT p.id, e.id AS etudiant_id, e.matricule, e.nom, e.prenoms,
                       f.libelle AS filiere, n.libelle AS niveau,
                       c.numero_candidat, p.jour_libelle, p.heure_pointage,
                       p.statut, p.qr_code_valide, p.created_at
                FROM pointage_journalier p
                JOIN etudiant e ON p.etudiant_id = e.id
                LEFT JOIN filiere f ON e.filiere_id = f.id
                LEFT JOIN niveau n ON e.niveau_id = n.id
                LEFT JOIN candidat_concours c ON e.id = c.etudiant_id
                WHERE p.date_pointage = :date
                ORDER BY p.heure_pointage ASC";

        $stmt = $pdo->prepare($sql);
        $stmt->bindValue(':date', $date, PDO::PARAM_STR);
        $stmt->execute();
        $rows = $stmt->fetchAll();

        // Statistiques rapides
        $totalPresents = count($rows);
        $totalInscrits = 0;
        try {
            $stmtInscrits = $pdo->query("SELECT COUNT(*) AS total FROM candidat_concours");
            $totalInscrits = (int) $stmtInscrits->fetchColumn();
        } catch (PDOException $e) {
            // La table candidat_concours peut ne pas exister encore
        }

        $taux = $totalInscrits > 0 ? round(($totalPresents / $totalInscrits) * 100, 2) : 0;

        respond(200, [
            "status"     => "success",
            "date"       => $date,
            "jour"       => $jour,
            "stats"      => [
                "total_presents"  => $totalPresents,
                "total_inscrits"  => $totalInscrits,
                "taux_presence"   => $taux . " %",
            ],
            "data"       => $rows,
        ]);

    } catch (PDOException $e) {
        respond(500, ["status" => "error", "message" => "Erreur base de données : " . $e->getMessage()]);
    }
}

// POST : enregistrer un pointage (scan QR Code)
// Body JSON : { matricule: "KOUA1703980001", date: "2026-06-10", jour_libelle: "Jour 1" }
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $data = json_decode(file_get_contents("php://input"), true);

    if (!$data || !isset($data['matricule']) || empty(trim($data['matricule']))) {
        respond(400, ["status" => "error", "message" => "Le champ 'matricule' est requis."]);
    }

    $matricule  = trim($data['matricule']);
    $date       = $data['date'] ?? date('Y-m-d');
    $jourLibelle= $data['jour_libelle'] ?? null;

    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $date)) {
        respond(400, ["status" => "error", "message" => "Date invalide. Format attendu : AAAA-MM-JJ."]);
    }

    try {
        // 1. Vérifier que l'étudiant existe (matricule)
        $stmt = $pdo->prepare("SELECT id FROM etudiant WHERE matricule = :matricule");
        $stmt->bindValue(':matricule', $matricule, PDO::PARAM_STR);
        $stmt->execute();
        $etudiant = $stmt->fetch();

        if (!$etudiant) {
            respond(404, ["status" => "error", "message" => "Matricule introuvable dans la base UPB."]);
        }

        $etudiantId = $etudiant['id'];

        // 2. Vérifier s'il est déjà pointé ce jour-là (double pointage)
        $stmtCheck = $pdo->prepare(
            "SELECT id, statut FROM pointage_journalier WHERE etudiant_id = :etudiant_id AND date_pointage = :date"
        );
        $stmtCheck->bindValue(':etudiant_id', $etudiantId, PDO::PARAM_INT);
        $stmtCheck->bindValue(':date', $date, PDO::PARAM_STR);
        $stmtCheck->execute();
        $dejaPoint = $stmtCheck->fetch();

        if ($dejaPoint) {
            respond(409, [
                "status"  => "error",
                "message" => "Double pointage détecté : cet étudiant a déjà été pointé le " . $date . ".",
                "data"    => ["statut_existant" => $dejaPoint['statut']]
            ]);
        }

        // 3. Insérer le pointage
        $stmtInsert = $pdo->prepare(
            "INSERT INTO pointage_journalier
                (etudiant_id, date_pointage, jour_libelle, heure_pointage, statut, qr_code_valide, created_at)
             VALUES
                (:etudiant_id, :date_pointage, :jour_libelle, NOW(), 'PRESENT', TRUE, NOW())"
        );
        $stmtInsert->bindValue(':etudiant_id', $etudiantId, PDO::PARAM_INT);
        $stmtInsert->bindValue(':date_pointage', $date, PDO::PARAM_STR);
        $stmtInsert->bindValue(':jour_libelle', $jourLibelle, PDO::PARAM_STR);
        $stmtInsert->execute();

        $nouvelId = $pdo->lastInsertId();

        respond(201, [
            "status"  => "success",
            "message" => "Présence enregistrée avec succès pour le matricule " . escHtml($matricule) . ".",
            "data"    => [
                "id"             => (int) $nouvelId,
                "etudiant_id"    => $etudiantId,
                "matricule"      => $matricule,
                "date_pointage"  => $date,
                "statut"         => "PRESENT",
                "heure_pointage" => date('Y-m-d H:i:s'),
            ],
        ]);

    } catch (PDOException $e) {
        // Contrainte d'unicité violée (course condition sur le double pointage)
        if (strpos($e->getMessage(), 'uq_etudiant_date') !== false) {
            respond(409, [
                "status"  => "error",
                "message" => "Double pointage détecté : cet étudiant a déjà été pointé ce jour.",
            ]);
        }
        respond(500, ["status" => "error", "message" => "Erreur base de données : " . $e->getMessage()]);
    }
}

// Méthode non supportée
respond(405, ["status" => "error", "message" => "Méthode non autorisée."]);

/**
 * Échappement HTML minimal pour les messages
 */
function escHtml(string $s): string {
    return htmlspecialchars($s, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}
