INSERT INTO patients VALUES (1,'Aster Patient','North','{"age":"70"}'),(2,'Boreal Patient','South','{"age":"45"}'),(3,'Cinder Patient','East','{"age":"66"}'),(4,'Dune Patient','West','{"age":"30"}');
INSERT INTO providers VALUES (1,'Riley','cardiology'),(2,'Morgan','radiology'),(3,'Taylor','primary');
INSERT INTO encounters VALUES (101,1,1,'2026-06-01 09:00+00','completed'),(102,1,2,'2026-06-10 09:00+00','scheduled'),(103,2,2,'2026-06-02 09:00+00','completed'),(104,3,3,'2026-05-01 09:00+00','cancelled');
INSERT INTO procedures VALUES (201,101,'XR','2026-06-01 09:30+00'),(202,101,'LAB','2026-06-01 10:00+00'),(203,103,'CT','2026-06-02 10:00+00');
INSERT INTO charges VALUES (301,201,100,'posted'),(302,202,50,'open'),(303,203,250,'posted');
INSERT INTO payments VALUES (401,1,'2026-06-05',75,'posted'),(402,2,'2026-06-05',250,'pending');
INSERT INTO diagnoses VALUES (501,101,'E11'),(502,103,'J10');
INSERT INTO external_directory VALUES (601,1,'patient@example.invalid');
