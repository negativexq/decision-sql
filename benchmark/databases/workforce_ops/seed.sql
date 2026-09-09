INSERT INTO teams VALUES (1,'Platform'),(2,'Operations'),(3,'Field');
INSERT INTO employees VALUES (1,'Aster Worker',1,'active','{"risk_score":"2"}'),(2,'Boreal Worker',2,'active','{"risk_score":"4"}'),(3,'Cinder Worker',3,'inactive','{"risk_score":"1"}'),(4,'Dune Worker',1,'active','{"risk_score":"3"}');
INSERT INTO shifts VALUES (101,1,'2026-06-01 08:00+00','2026-06-01 16:00+00'),(102,2,'2026-06-02 08:00+00','2026-06-02 16:00+00'),(103,4,'2026-05-01 08:00+00','2026-05-01 16:00+00');
INSERT INTO timesheets VALUES (201,1,'2026-06-01',8,true),(202,1,'2026-06-02',4,false),(203,2,'2026-06-02',8,true),(204,4,'2026-05-01',8,true);
INSERT INTO absences VALUES (301,1,'2026-06-10','2026-06-11','approved'),(302,2,'2026-06-12','2026-06-12','pending');
INSERT INTO training_records VALUES (401,1,'Safety','2026-05-01'),(402,2,'Safety','2026-06-03'),(403,4,'Security','2025-01-01');
INSERT INTO payroll_adjustments VALUES (501,1,100,'2026-06-05','bonus'),(502,2,-20,'2026-06-05','deduction');
INSERT INTO external_directory VALUES (601,1,'employee@example.invalid');
