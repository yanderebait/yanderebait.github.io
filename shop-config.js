// The shop's back end (Supabase).
// Both values are public by design. They only say WHERE the shop's data lives.
// The rules in supabase/setup.sql decide who is allowed to change it.
// While these are empty, the shop page says it isn't open yet.
window.SHOP_CONFIG = {
  supabaseUrl: "",
  supabaseKey: ""
};
