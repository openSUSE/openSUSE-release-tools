for file in *.erb; do
  erb -T - $file > $(basename $file .erb)
done
